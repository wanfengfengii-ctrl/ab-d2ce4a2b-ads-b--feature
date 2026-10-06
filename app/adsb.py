"""Mode S / ADS-B frame inspection: CRC parity, DF17 airborne positions."""
from __future__ import annotations

from dataclasses import dataclass

from .cpr import encode_position
from .errors import DecodeError

MESSAGE_BITS = 112
MESSAGE_HEX_LEN = 28

# Mode S CRC generator polynomial G(x) = x^24 + x^23 + ... + x^12 + x^10 + x^3 + 1
# (binary 1 1111 1111 1111 0100 0000 1001), used for the 24-bit parity field.
CRC_POLY = 0x1FFF409

DF_EXTENDED_SQUITTER = 17

# Type codes carrying an airborne position: 9-18 (barometric altitude) and
# 20-22 (GNSS height).  Both share the same CPR latitude/longitude layout.
AIRBORNE_POSITION_TCS = frozenset(range(9, 19)) | {20, 21, 22}


def crc_remainder(value: int, nbits: int = MESSAGE_BITS) -> int:
    """Remainder of ``value`` (an ``nbits``-long word) over the Mode S CRC.

    A DF17 frame is parity-clean when the remainder of the full 112-bit
    message is zero.
    """
    for i in range(nbits - 25, -1, -1):
        if value & (1 << (i + 24)):
            value ^= CRC_POLY << i
    return value & 0xFFFFFF


@dataclass(frozen=True)
class PositionFrame:
    """Parsed DF17 airborne-position frame."""

    raw: str
    time_ms: int
    icao: str
    type_code: int
    odd: bool
    lat_cpr: int
    lon_cpr: int
    altitude_field: int  # raw 12-bit altitude code (ME bits 9-20)


def parse_position_frame(raw: str, time_ms: int, index: int | None = None) -> PositionFrame:
    """Validate and parse one 28-hex-char frame.

    Raises :class:`DecodeError` with a stable code on any failure.
    """
    where = f"frame {index}: " if index is not None else ""
    text = raw.strip() if isinstance(raw, str) else ""
    if len(text) != MESSAGE_HEX_LEN:
        raise DecodeError(
            "INVALID_MESSAGE",
            f"{where}expected {MESSAGE_HEX_LEN} hex characters, got {len(text)}",
        )
    try:
        bits = int(text, 16)
    except ValueError:
        raise DecodeError("INVALID_MESSAGE", f"{where}message is not hexadecimal") from None

    if crc_remainder(bits) != 0:
        raise DecodeError("CRC_MISMATCH", f"{where}CRC parity check failed")

    df = bits >> (MESSAGE_BITS - 5)
    if df != DF_EXTENDED_SQUITTER:
        raise DecodeError("NOT_DF17", f"{where}downlink format is {df}, expected 17")

    icao = f"{(bits >> 80) & 0xFFFFFF:06X}"
    me = (bits >> 24) & ((1 << 56) - 1)
    type_code = me >> 51
    if type_code not in AIRBORNE_POSITION_TCS:
        raise DecodeError(
            "NOT_AIRBORNE_POSITION",
            f"{where}type code {type_code} is not an airborne position (9-18, 20-22)",
        )

    return PositionFrame(
        raw=text.upper(),
        time_ms=time_ms,
        icao=icao,
        type_code=type_code,
        odd=bool((me >> 34) & 1),
        lat_cpr=(me >> 17) & 0x1FFFF,
        lon_cpr=me & 0x1FFFF,
        altitude_field=(me >> 36) & 0xFFF,
    )


def build_message(df: int, icao: int, me: int, ca: int = 5) -> str:
    """Assemble a 112-bit Mode S frame with a valid CRC parity field."""
    head = (
        ((df & 0x1F) << 107)
        | ((ca & 0x7) << 104)
        | ((icao & 0xFFFFFF) << 80)
        | ((me & ((1 << 56) - 1)) << 24)
    )
    return f"{head | crc_remainder(head):028X}"


def build_position_message(
    icao: str | int,
    lat: float,
    lon: float,
    odd: bool,
    type_code: int = 11,
    altitude_field: int = 0,
) -> str:
    """Build a synthetic DF17 airborne-position frame (tests/smoke helper).

    ``altitude_field`` is the raw 12-bit altitude code; see
    :mod:`app.altitude` for helpers that encode feet/metres into it.
    """
    if isinstance(icao, str):
        icao = int(icao, 16)
    yz, xz = encode_position(lat, lon, odd)
    me = (
        ((type_code & 0x1F) << 51)
        | ((altitude_field & 0xFFF) << 36)
        | ((1 if odd else 0) << 34)
        | ((yz & 0x1FFFF) << 17)
        | (xz & 0x1FFFF)
    )
    return build_message(DF_EXTENDED_SQUITTER, icao, me)
