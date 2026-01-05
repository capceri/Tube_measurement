import math
import threading
import time
from typing import List, Optional

from al1322_client import AL1322Client, MockAL1322Client
from config_store import ConfigStore, mm_to_in
from conversion import ConversionError, convert_hex
from state import LogBuffer, StateStore

CONSTANT_LENGTH_MM = 1165.0
NX_RED = 63488
NX_GREEN = 2016


def within_tol(value: float, target: float, tol: float) -> bool:
    return math.isfinite(value) and math.isfinite(target) and math.isfinite(tol) and abs(value - target) <= tol


def within_max_abs(value: float, max_value: float) -> bool:
    return math.isfinite(value) and math.isfinite(max_value) and abs(value) <= max_value


def within_max(value: float, max_value: float) -> bool:
    return math.isfinite(value) and math.isfinite(max_value) and value <= max_value


def range_of_three(a: float, b: float, c: float) -> Optional[float]:
    if not (math.isfinite(a) and math.isfinite(b) and math.isfinite(c)):
        return None
    return max(a, b, c) - min(a, b, c)


class MeasurementEngine:
    def __init__(
        self,
        config_store: ConfigStore,
        state_store: StateStore,
        log_buffer: LogBuffer,
        hmi_handler,
        mock_mode: bool = False,
    ) -> None:
        self._config_store = config_store
        self._state_store = state_store
        self._log = log_buffer
        self._hmi = hmi_handler
        self._mock_mode = mock_mode
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client = MockAL1322Client() if mock_mode else AL1322Client(config_store.snapshot().al1322_ip)
        self._last_config_version = -1

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="measurement-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(0.01)
                continue
            config = self._config_store.snapshot()
            if not self._mock_mode:
                self._client = AL1322Client(config.al1322_ip, timeout_s=config.request_timeout_s)

            results = []
            all_ok = True
            for port in range(1, 9):
                result = self._client.read_port_get(port)
                results.append(result)
                if not result.ok:
                    all_ok = False
                    self._log.add("ERROR", f"AL1322 port {port} read failed: {result.error}", "al1322")

            converted_raw: List[Optional[float]] = [None] * 8
            converted_mm: List[float] = [math.nan] * 8
            raw_hex: List[Optional[str]] = [None] * 8
            for idx, result in enumerate(results):
                raw_hex[idx] = result.raw_hex
                if result.ok and result.raw_hex:
                    try:
                        raw_value, value_mm = convert_hex(result.raw_hex, config.channels[idx])
                        converted_raw[idx] = raw_value
                        converted_mm[idx] = value_mm
                    except ConversionError as exc:
                        all_ok = False
                        self._log.add("ERROR", f"Port {idx + 1} conversion error: {exc}", "conversion")
                else:
                    converted_mm[idx] = math.nan

            with_offsets = []
            for idx, value in enumerate(converted_mm):
                if math.isfinite(value):
                    with_offsets.append(value + config.offsets_mm[idx])
                else:
                    with_offsets.append(math.nan)

            d1 = with_offsets[0]
            d2 = with_offsets[1]
            d_delta = (d1 - d2) if (math.isfinite(d1) and math.isfinite(d2)) else math.nan
            end1_rng = range_of_three(with_offsets[2], with_offsets[3], with_offsets[4])
            end2_rng = range_of_three(with_offsets[5], with_offsets[6], with_offsets[7])
            length = (
                CONSTANT_LENGTH_MM - (with_offsets[2] + with_offsets[5])
                if (math.isfinite(with_offsets[2]) and math.isfinite(with_offsets[5]))
                else math.nan
            )

            ok_d1 = within_tol(d1, config.targets.d1_target, config.targets.d1_tol)
            ok_d2 = within_tol(d2, config.targets.d2_target, config.targets.d2_tol)
            ok_dd = within_max_abs(d_delta, config.targets.dDelta_max)
            ok_e1 = within_max(end1_rng if end1_rng is not None else math.nan, config.targets.end1_max)
            ok_e2 = within_max(end2_rng if end2_rng is not None else math.nan, config.targets.end2_max)
            ok_len = within_tol(length, config.targets.len_target, config.targets.len_tol)
            overall = all_ok and ok_d1 and ok_d2 and ok_dd and ok_e1 and ok_e2 and ok_len

            now_ts = time.time()
            def _update_state(state):
                state.raw_hex = raw_hex
                state.raw_values = converted_raw
                state.values_mm = converted_mm
                state.values_in = [mm_to_in(v) if math.isfinite(v) else math.nan for v in converted_mm]
                state.metrics_mm = {
                    "d1": d1,
                    "d2": d2,
                    "dDelta": d_delta,
                    "end1_rng": end1_rng if end1_rng is not None else math.nan,
                    "end2_rng": end2_rng if end2_rng is not None else math.nan,
                    "length": length,
                }
                state.metrics_in = {
                    "d1": mm_to_in(d1) if math.isfinite(d1) else math.nan,
                    "d2": mm_to_in(d2) if math.isfinite(d2) else math.nan,
                    "dDelta": mm_to_in(d_delta) if math.isfinite(d_delta) else math.nan,
                    "end1_rng": mm_to_in(end1_rng) if end1_rng is not None else math.nan,
                    "end2_rng": mm_to_in(end2_rng) if end2_rng is not None else math.nan,
                    "length": mm_to_in(length) if math.isfinite(length) else math.nan,
                }
                state.checks = {
                    "d1": ok_d1,
                    "d2": ok_d2,
                    "dDelta": ok_dd,
                    "end1_rng": ok_e1,
                    "end2_rng": ok_e2,
                    "length": ok_len,
                }
                state.overall_pass = overall
                state.last_cycle_ts = now_ts
                if all_ok:
                    state.last_ok_ts = now_ts
                state.mock_mode = self._mock_mode
                for idx, result in enumerate(results):
                    port_state = state.port_status[idx]
                    port_state.last_call_ts = now_ts
                    if result.ok:
                        port_state.last_ok_ts = now_ts
                        port_state.last_error = None
                        port_state.last_http_status = result.http_status
                        port_state.last_raw_hex = result.raw_hex
                    else:
                        port_state.error_count += 1
                        port_state.last_error = result.error
                        port_state.last_http_status = result.http_status
                        port_state.last_raw_hex = None

            self._state_store.update_state(_update_state)

            if self._hmi:
                self._hmi.update_live(
                    d1=d1,
                    d2=d2,
                    d_delta=d_delta,
                    end1_rng=end1_rng,
                    end2_rng=end2_rng,
                    length=length,
                    ok_d1=ok_d1,
                    ok_d2=ok_d2,
                    ok_dd=ok_dd,
                    ok_e1=ok_e1,
                    ok_e2=ok_e2,
                    ok_len=ok_len,
                    overall=overall,
                )

                current_version = self._config_store.version
                if current_version != self._last_config_version:
                    self._hmi.send_targets(config.targets)
                    self._hmi.send_offsets(config.offsets_mm)
                    self._last_config_version = current_version

            next_tick = now + config.poll_interval_s

