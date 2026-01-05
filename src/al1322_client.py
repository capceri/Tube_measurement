import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

HEX_VALUE_RE = re.compile(r"^0x[0-9a-fA-F]+$|^[0-9a-fA-F]+$")


@dataclass
class PortReadResult:
    port: int
    ok: bool
    raw_hex: Optional[str]
    error: Optional[str]
    http_status: Optional[int]
    response_time_s: Optional[float]


class AL1322Client:
    def __init__(self, ip: str, timeout_s: float = 1.0) -> None:
        self.base_url = f"http://{ip}"
        self.timeout_s = timeout_s

    def _extract_hex(self, payload: Any) -> Optional[str]:
        if isinstance(payload, str):
            value = payload.strip()
            if HEX_VALUE_RE.match(value):
                return value
            return None
        if isinstance(payload, dict):
            for key in ("data", "value", "pDIN", "pdin", "hex"):
                if key in payload and isinstance(payload[key], str):
                    value = payload[key].strip()
                    if HEX_VALUE_RE.match(value):
                        return value
            for value in payload.values():
                found = self._extract_hex(value)
                if found:
                    return found
        if isinstance(payload, list):
            for item in payload:
                found = self._extract_hex(item)
                if found:
                    return found
        return None

    def _parse_response(self, text: str) -> Optional[str]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return self._extract_hex(text)
        return self._extract_hex(payload)

    def read_port_get(self, port: int) -> PortReadResult:
        url = f"{self.base_url}/iolinkmaster/port[{port}]/iolinkdevice/pdin/getdata"
        start = time.monotonic()
        try:
            resp = requests.get(url, timeout=self.timeout_s)
            elapsed = time.monotonic() - start
            hex_value = self._parse_response(resp.text)
            if resp.status_code != 200:
                return PortReadResult(port, False, None, f"HTTP {resp.status_code}", resp.status_code, elapsed)
            if not hex_value:
                return PortReadResult(port, False, None, "No hex data in response", resp.status_code, elapsed)
            return PortReadResult(port, True, hex_value, None, resp.status_code, elapsed)
        except requests.RequestException as exc:
            elapsed = time.monotonic() - start
            return PortReadResult(port, False, None, str(exc), None, elapsed)

    def read_port_post(self, port: int) -> PortReadResult:
        url = f"{self.base_url}/iolinkmaster/port[{port}]/iolinkdevice/pdin/getdata"
        start = time.monotonic()
        try:
            resp = requests.post(url, json={}, timeout=self.timeout_s)
            elapsed = time.monotonic() - start
            hex_value = self._parse_response(resp.text)
            if resp.status_code != 200:
                return PortReadResult(port, False, None, f"HTTP {resp.status_code}", resp.status_code, elapsed)
            if not hex_value:
                return PortReadResult(port, False, None, "No hex data in response", resp.status_code, elapsed)
            return PortReadResult(port, True, hex_value, None, resp.status_code, elapsed)
        except requests.RequestException as exc:
            elapsed = time.monotonic() - start
            return PortReadResult(port, False, None, str(exc), None, elapsed)


class MockAL1322Client:
    def __init__(self) -> None:
        self._tick = 0

    def _format_hex(self, value: int) -> str:
        return f"0x{value:08X}"

    def read_port_get(self, port: int) -> PortReadResult:
        self._tick += 1
        base_um = [0, 0, 0, 5, 10, 0, 5, 10]
        value = base_um[port - 1]
        return PortReadResult(port, True, self._format_hex(value), None, 200, 0.0)

    def read_port_post(self, port: int) -> PortReadResult:
        return self.read_port_get(port)

