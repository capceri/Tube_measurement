import json
import os
import threading
from copy import deepcopy
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

MM_PER_IN = 25.4


def in_to_mm(value_in: float) -> float:
    return value_in * MM_PER_IN


def mm_to_in(value_mm: float) -> float:
    return value_mm / MM_PER_IN


@dataclass
class Targets:
    d1_target: float = 0.0
    d1_tol: float = 0.050
    d2_target: float = 0.0
    d2_tol: float = 0.050
    len_target: float = 1165.0
    len_tol: float = 0.200
    dDelta_max: float = 0.050
    end1_max: float = 0.050
    end2_max: float = 0.050


@dataclass
class ChannelConfig:
    raw_format: str = "uint_be"
    scale: float = 0.001
    offset: float = 0.0
    start_bit: Optional[int] = None
    bit_length: Optional[int] = None


@dataclass
class HMIConfig:
    serial_port: str = "/dev/serial0"
    baud: int = 115200


@dataclass
class Config:
    al1322_ip: str = "192.168.100.1"
    poll_interval_s: float = 0.5
    request_timeout_s: float = 1.0
    log_capacity: int = 200
    hmi: HMIConfig = field(default_factory=HMIConfig)
    targets: Targets = field(default_factory=Targets)
    offsets_mm: List[float] = None
    channels: List[ChannelConfig] = None

    def __post_init__(self) -> None:
        if self.offsets_mm is None:
            self.offsets_mm = [0.0] * 8
        if self.channels is None:
            self.channels = [ChannelConfig() for _ in range(8)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "al1322_ip": self.al1322_ip,
            "poll_interval_s": self.poll_interval_s,
            "request_timeout_s": self.request_timeout_s,
            "log_capacity": self.log_capacity,
            "hmi": asdict(self.hmi),
            "targets": asdict(self.targets),
            "offsets_mm": list(self.offsets_mm),
            "channels": [asdict(c) for c in self.channels],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        cfg = cls()
        cfg.al1322_ip = data.get("al1322_ip", cfg.al1322_ip)
        cfg.poll_interval_s = float(data.get("poll_interval_s", cfg.poll_interval_s))
        cfg.request_timeout_s = float(data.get("request_timeout_s", cfg.request_timeout_s))
        cfg.log_capacity = int(data.get("log_capacity", cfg.log_capacity))

        hmi_data = data.get("hmi", {})
        cfg.hmi = HMIConfig(
            serial_port=hmi_data.get("serial_port", cfg.hmi.serial_port),
            baud=int(hmi_data.get("baud", cfg.hmi.baud)),
        )

        targets_data = data.get("targets", {})
        cfg.targets = Targets(
            d1_target=float(targets_data.get("d1_target", cfg.targets.d1_target)),
            d1_tol=float(targets_data.get("d1_tol", cfg.targets.d1_tol)),
            d2_target=float(targets_data.get("d2_target", cfg.targets.d2_target)),
            d2_tol=float(targets_data.get("d2_tol", cfg.targets.d2_tol)),
            len_target=float(targets_data.get("len_target", cfg.targets.len_target)),
            len_tol=float(targets_data.get("len_tol", cfg.targets.len_tol)),
            dDelta_max=float(targets_data.get("dDelta_max", cfg.targets.dDelta_max)),
            end1_max=float(targets_data.get("end1_max", cfg.targets.end1_max)),
            end2_max=float(targets_data.get("end2_max", cfg.targets.end2_max)),
        )

        offsets = data.get("offsets_mm", cfg.offsets_mm)
        cfg.offsets_mm = [float(v) for v in offsets][:8]
        if len(cfg.offsets_mm) < 8:
            cfg.offsets_mm += [0.0] * (8 - len(cfg.offsets_mm))

        channels = data.get("channels", [])
        cfg.channels = []
        for idx in range(8):
            ch = channels[idx] if idx < len(channels) else {}
            cfg.channels.append(
                ChannelConfig(
                    raw_format=str(ch.get("raw_format", "uint_be")),
                    scale=float(ch.get("scale", 0.001)),
                    offset=float(ch.get("offset", 0.0)),
                    start_bit=ch.get("start_bit"),
                    bit_length=ch.get("bit_length"),
                )
            )
        return cfg


class ConfigStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._config = Config()
        self._version = 0
        self.load()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def snapshot(self) -> Config:
        with self._lock:
            return deepcopy(self._config)

    def load(self) -> None:
        with self._lock:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._config = Config.from_dict(data)
            else:
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                self._config = Config()
                self.save()
            self._version += 1

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._config.to_dict(), f, indent=2, sort_keys=False)

    def replace_config(self, config: Config) -> None:
        with self._lock:
            self._config = config
            self._version += 1

    def update_targets_mm(self, updates: Dict[str, float]) -> None:
        with self._lock:
            for key, value in updates.items():
                if hasattr(self._config.targets, key):
                    setattr(self._config.targets, key, float(value))
            self._version += 1

    def update_offsets_mm(self, offsets_mm: List[float]) -> None:
        with self._lock:
            self._config.offsets_mm = [float(v) for v in offsets_mm][:8]
            if len(self._config.offsets_mm) < 8:
                self._config.offsets_mm += [0.0] * (8 - len(self._config.offsets_mm))
            self._version += 1

    def update_from_hmi_set(self, key: str, value_in: float) -> bool:
        value_mm = in_to_mm(value_in)
        with self._lock:
            if key == "d1t":
                self._config.targets.d1_target = value_mm
            elif key == "d1tol":
                self._config.targets.d1_tol = value_mm
            elif key == "d2t":
                self._config.targets.d2_target = value_mm
            elif key == "d2tol":
                self._config.targets.d2_tol = value_mm
            elif key == "lent":
                self._config.targets.len_target = value_mm
            elif key == "lentol":
                self._config.targets.len_tol = value_mm
            elif key == "ddelmax":
                self._config.targets.dDelta_max = value_mm
            elif key == "e1max":
                self._config.targets.end1_max = value_mm
            elif key == "e2max":
                self._config.targets.end2_max = value_mm
            elif key.startswith("off"):
                try:
                    idx = int(key[3:])
                except ValueError:
                    return False
                if 0 <= idx < 8:
                    self._config.offsets_mm[idx] = value_mm
                else:
                    return False
            else:
                return False
            self._version += 1
        return True

    def update_from_form(self, form: Dict[str, str]) -> None:
        updates = {}
        for field, key in [
            ("d1_target_in", "d1_target"),
            ("d1_tol_in", "d1_tol"),
            ("d2_target_in", "d2_target"),
            ("d2_tol_in", "d2_tol"),
            ("len_target_in", "len_target"),
            ("len_tol_in", "len_tol"),
            ("ddelta_max_in", "dDelta_max"),
            ("end1_max_in", "end1_max"),
            ("end2_max_in", "end2_max"),
        ]:
            if field in form:
                updates[key] = in_to_mm(float(form[field]))

        offsets = []
        for idx in range(8):
            field = f"off{idx}_in"
            if field in form:
                offsets.append(in_to_mm(float(form[field])))
        if offsets:
            self.update_offsets_mm(offsets)

        if updates:
            self.update_targets_mm(updates)
