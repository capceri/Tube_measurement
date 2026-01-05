import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


@dataclass
class PortStatus:
    port: int
    last_call_ts: Optional[float] = None
    last_ok_ts: Optional[float] = None
    last_error: Optional[str] = None
    last_http_status: Optional[int] = None
    last_raw_hex: Optional[str] = None
    error_count: int = 0


class LogBuffer:
    def __init__(self, capacity: int = 200) -> None:
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def add(self, level: str, message: str, source: str) -> None:
        entry = {
            "ts": time.time(),
            "level": level,
            "message": message,
            "source": source,
        }
        with self._lock:
            self._buffer.append(entry)

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._buffer)


@dataclass
class MeasurementState:
    raw_hex: List[Optional[str]] = field(default_factory=lambda: [None] * 8)
    raw_values: List[Optional[float]] = field(default_factory=lambda: [None] * 8)
    values_mm: List[float] = field(default_factory=lambda: [math.nan] * 8)
    values_in: List[float] = field(default_factory=lambda: [math.nan] * 8)
    metrics_mm: Dict[str, float] = field(default_factory=dict)
    metrics_in: Dict[str, float] = field(default_factory=dict)
    checks: Dict[str, Optional[bool]] = field(default_factory=dict)
    overall_pass: bool = False
    last_cycle_ts: Optional[float] = None
    last_ok_ts: Optional[float] = None
    mock_mode: bool = False
    port_status: List[PortStatus] = field(default_factory=lambda: [PortStatus(port=i + 1) for i in range(8)])

    def __post_init__(self) -> None:
        if not self.metrics_mm:
            self.metrics_mm = {
                "d1": math.nan,
                "d2": math.nan,
                "dDelta": math.nan,
                "end1_rng": math.nan,
                "end2_rng": math.nan,
                "length": math.nan,
            }
        if not self.metrics_in:
            self.metrics_in = {
                "d1": math.nan,
                "d2": math.nan,
                "dDelta": math.nan,
                "end1_rng": math.nan,
                "end2_rng": math.nan,
                "length": math.nan,
            }
        if not self.checks:
            self.checks = {
                "d1": None,
                "d2": None,
                "dDelta": None,
                "end1_rng": None,
                "end2_rng": None,
                "length": None,
            }


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = MeasurementState()

    def snapshot(self) -> MeasurementState:
        with self._lock:
            return MeasurementState(
                raw_hex=list(self._state.raw_hex),
                raw_values=list(self._state.raw_values),
                values_mm=list(self._state.values_mm),
                values_in=list(self._state.values_in),
                metrics_mm=dict(self._state.metrics_mm),
                metrics_in=dict(self._state.metrics_in),
                checks=dict(self._state.checks),
                overall_pass=self._state.overall_pass,
                last_cycle_ts=self._state.last_cycle_ts,
                last_ok_ts=self._state.last_ok_ts,
                mock_mode=self._state.mock_mode,
                port_status=[
                    PortStatus(
                        port=p.port,
                        last_call_ts=p.last_call_ts,
                        last_ok_ts=p.last_ok_ts,
                        last_error=p.last_error,
                        last_http_status=p.last_http_status,
                        last_raw_hex=p.last_raw_hex,
                        error_count=p.error_count,
                    )
                    for p in self._state.port_status
                ],
            )

    def update_state(self, updater) -> None:
        with self._lock:
            updater(self._state)

