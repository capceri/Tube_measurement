import math
import threading
import time
from typing import List, Optional

import serial

from config_store import ConfigStore, Targets, mm_to_in
from state import LogBuffer

NX_RED = 63488
NX_GREEN = 2016


class HMIHandler:
    def __init__(self, config_store: ConfigStore, log_buffer: LogBuffer) -> None:
        self._config_store = config_store
        self._log = log_buffer
        self._serial: Optional[serial.Serial] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._buf = bytearray()
        self._ff_count = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hmi-serial", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            config = self._config_store.snapshot()
            if config.hmi.serial_port.upper() == "DISABLED":
                time.sleep(1.0)
                continue
            try:
                with serial.Serial(
                    config.hmi.serial_port,
                    config.hmi.baud,
                    timeout=0.1,
                ) as ser:
                    self._serial = ser
                    self._log.add("INFO", f"HMI connected on {config.hmi.serial_port}", "hmi")
                    self._send_init()
                    self._read_loop(ser)
            except serial.SerialException as exc:
                self._serial = None
                self._log.add("ERROR", f"HMI serial error: {exc}", "hmi")
                time.sleep(2.0)

    def _send_init(self) -> None:
        self.send_command("bkcmd=3")
        self.send_command("page op")
        self.send_command("sendme")
        config = self._config_store.snapshot()
        self.send_targets(config.targets)
        self.send_offsets(config.offsets_mm)

    def _read_loop(self, ser: serial.Serial) -> None:
        while not self._stop_event.is_set():
            try:
                data = ser.read(64)
            except serial.SerialException as exc:
                self._log.add("ERROR", f"HMI serial read error: {exc}", "hmi")
                break
            if not data:
                continue
            for byte in data:
                if byte == 0xFF:
                    self._ff_count += 1
                    if self._ff_count >= 3:
                        self._process_frame(bytes(self._buf))
                        self._buf.clear()
                        self._ff_count = 0
                    continue
                self._ff_count = 0
                if len(self._buf) < 256:
                    self._buf.append(byte)

    def _process_frame(self, frame: bytes) -> None:
        if not frame:
            return
        lead = frame[0]
        if lead == 0x70:
            text = frame[1:].split(b"\x00", 1)[0].decode(errors="ignore")
            self._log.add("INFO", f"HMI text reply: {text}", "hmi")
            return
        if 32 <= lead <= 126:
            line = frame.decode(errors="ignore").strip()
            self._handle_line(line)
            return
        self._log.add("INFO", f"HMI binary frame lead=0x{lead:02X}", "hmi")

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        self._log.add("INFO", f"HMI command: {line}", "hmi")
        parts = line.split()
        if not parts:
            return
        cmd = parts[0].upper()
        if cmd == "SET" and len(parts) >= 2:
            key = parts[1]
            val_token = parts[2] if len(parts) >= 3 else ""
            if len(parts) == 2:
                payload = line[len(parts[0]) :].strip()
                if "=" in payload:
                    key, val_token = payload.split("=", 1)
                elif ":" in payload:
                    key, val_token = payload.split(":", 1)
                elif "," in payload:
                    key, val_token = payload.split(",", 1)
                else:
                    self._log.add("ERROR", f"Invalid SET format: {line}", "hmi")
                    return
            key = key.strip().lower()
            val_token = val_token.strip()
            try:
                value_in = float(val_token)
            except ValueError:
                self._log.add("ERROR", f"Invalid SET value: {val_token}", "hmi")
                return
            updated = self._config_store.update_from_hmi_set(key, value_in)
            if not updated:
                self._log.add("ERROR", f"Unknown SET key: {key}", "hmi")
            return
        if cmd == "SAVE":
            self._config_store.save()
            self._log.add("INFO", "Config saved via HMI", "hmi")
            return
        if cmd == "REQ" and len(parts) >= 2:
            req = parts[1].upper()
            config = self._config_store.snapshot()
            if req == "TARGETS":
                self.send_targets(config.targets)
            elif req == "OFFSETS":
                self.send_offsets(config.offsets_mm)
            return
        if cmd == "DUMP":
            config = self._config_store.snapshot()
            self._log.add("INFO", f"Config dump: {config.targets} offsets={config.offsets_mm}", "hmi")
            return

    def send_command(self, cmd: str) -> None:
        if not self._serial:
            return
        payload = cmd.encode("ascii", errors="ignore") + b"\xFF\xFF\xFF"
        with self._write_lock:
            try:
                self._serial.write(payload)
            except serial.SerialException as exc:
                self._log.add("ERROR", f"HMI write error: {exc}", "hmi")

    def _fmt_in(self, value_mm: float) -> str:
        if not math.isfinite(value_mm):
            return "-"
        return f"{mm_to_in(value_mm):.3f}"

    def update_live(
        self,
        d1: float,
        d2: float,
        d_delta: float,
        end1_rng: Optional[float],
        end2_rng: Optional[float],
        length: float,
        ok_d1: bool,
        ok_d2: bool,
        ok_dd: bool,
        ok_e1: bool,
        ok_e2: bool,
        ok_len: bool,
        overall: bool,
    ) -> None:
        self.send_command(f"tD1.txt=\"{self._fmt_in(d1)}\"")
        self.send_command(f"tD2.txt=\"{self._fmt_in(d2)}\"")
        self.send_command(f"tDelta.txt=\"{self._fmt_in(d_delta)}\"")
        self.send_command(f"tEnd1.txt=\"{self._fmt_in(end1_rng if end1_rng is not None else math.nan)}\"")
        self.send_command(f"tEnd2.txt=\"{self._fmt_in(end2_rng if end2_rng is not None else math.nan)}\"")
        self.send_command(f"tLen.txt=\"{self._fmt_in(length)}\"")

        self.send_command(f"tD1.pco={NX_GREEN if ok_d1 else NX_RED}")
        self.send_command(f"tD2.pco={NX_GREEN if ok_d2 else NX_RED}")
        self.send_command(f"tDelta.pco={NX_GREEN if ok_dd else NX_RED}")
        self.send_command(f"tEnd1.pco={NX_GREEN if ok_e1 else NX_RED}")
        self.send_command(f"tEnd2.pco={NX_GREEN if ok_e2 else NX_RED}")
        self.send_command(f"tLen.pco={NX_GREEN if ok_len else NX_RED}")

        self.send_command(f"tStatus.txt=\"{'PASS' if overall else 'FAIL'}\"")
        self.send_command(f"tStatus.pco={NX_GREEN if overall else NX_RED}")
        self.send_command(f"op.bco={NX_GREEN if overall else NX_RED}")
        self.send_command("ref op")

    def send_targets(self, targets: Targets) -> None:
        self.send_command(f"tD1Target.txt=\"{self._fmt_in(targets.d1_target)}\"")
        self.send_command(f"tD1Tol.txt=\"{self._fmt_in(targets.d1_tol)}\"")
        self.send_command(f"tD2Target.txt=\"{self._fmt_in(targets.d2_target)}\"")
        self.send_command(f"tD2Tol.txt=\"{self._fmt_in(targets.d2_tol)}\"")
        self.send_command(f"tLenTarget.txt=\"{self._fmt_in(targets.len_target)}\"")
        self.send_command(f"tLenTol.txt=\"{self._fmt_in(targets.len_tol)}\"")
        self.send_command(f"tDeltaMax.txt=\"{self._fmt_in(targets.dDelta_max)}\"")
        self.send_command(f"tEnd1Max.txt=\"{self._fmt_in(targets.end1_max)}\"")
        self.send_command(f"tEnd2Max.txt=\"{self._fmt_in(targets.end2_max)}\"")

    def send_offsets(self, offsets_mm: List[float]) -> None:
        for idx, value in enumerate(offsets_mm):
            self.send_command(f"tOff{idx}.txt=\"{self._fmt_in(value)}\"")
