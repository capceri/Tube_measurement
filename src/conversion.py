import math
import re
import struct
from typing import Optional, Tuple

from config_store import ChannelConfig

HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$|^[0-9a-fA-F]+$")
SENTINEL_RAW_MIN = 2147483640.0


class ConversionError(Exception):
    pass


def hex_to_bytes(hex_str: str) -> bytes:
    clean = hex_str.strip()
    if clean.startswith("0x") or clean.startswith("0X"):
        clean = clean[2:]
    if len(clean) % 2 == 1:
        clean = "0" + clean
    if not HEX_RE.match(clean):
        raise ConversionError(f"Invalid hex string: {hex_str}")
    return bytes.fromhex(clean)


def _apply_bit_slice(value: int, start_bit: int, bit_length: int, signed: bool) -> int:
    if bit_length <= 0:
        return value
    mask = (1 << bit_length) - 1
    sliced = (value >> start_bit) & mask
    if signed:
        sign_bit = 1 << (bit_length - 1)
        if sliced & sign_bit:
            sliced = sliced - (1 << bit_length)
    return sliced


def decode_raw_value(hex_str: str, cfg: ChannelConfig) -> float:
    raw_bytes = hex_to_bytes(hex_str)
    if cfg.raw_format in ("uint_be", "uint_le", "int_be", "int_le"):
        signed = cfg.raw_format.startswith("int_")
        byteorder = "big" if cfg.raw_format.endswith("be") else "little"
        value = int.from_bytes(raw_bytes, byteorder=byteorder, signed=signed)
        if cfg.start_bit is not None and cfg.bit_length is not None:
            value = _apply_bit_slice(value, int(cfg.start_bit), int(cfg.bit_length), signed)
        return float(value)

    if cfg.raw_format in ("float_be", "float_le"):
        byteorder = ">" if cfg.raw_format.endswith("be") else "<"
        if len(raw_bytes) == 4:
            fmt = f"{byteorder}f"
        elif len(raw_bytes) == 8:
            fmt = f"{byteorder}d"
        else:
            raise ConversionError(f"Unsupported float byte length: {len(raw_bytes)}")
        return float(struct.unpack(fmt, raw_bytes[: struct.calcsize(fmt)])[0])

    raise ConversionError(f"Unsupported raw_format: {cfg.raw_format}")


def convert_hex(hex_str: str, cfg: ChannelConfig) -> Tuple[Optional[float], float]:
    raw_value = decode_raw_value(hex_str, cfg)
    if not math.isfinite(raw_value) or raw_value >= SENTINEL_RAW_MIN:
        return None, math.nan
    value_mm = raw_value * cfg.scale + cfg.offset
    return raw_value, value_mm
