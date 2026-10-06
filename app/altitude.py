"""Altitude decoding for DF17 airborne-position frames.

Two different vertical references share the same 12-bit altitude field
(ME bits 9-20):

* Type codes 9-18 carry **barometric altitude in feet**, encoded either in
  25 ft increments (Q bit set) or as the Gillham/Gray-coded 100 ft form used
  above 50 187.5 ft.
* Type codes 20-22 carry the **GNSS height above the WGS-84 ellipsoid in
  metres**, a plain unsigned 12-bit integer.

Only the altitude of the *newer* frame is ever handed to the fusion layer,
so barometric and geometric heights are never mixed inside one result.
"""
from __future__ import annotations

from dataclasses import dataclass

from .adsb import PositionFrame
from .errors import DecodeError

#: GNSS height type codes (field is an unsigned 12-bit metre count).
GNSS_TYPE_CODES = frozenset({20, 21, 22})

#: Lowest encodable Gillham altitude (Mode C index -12).
MIN_GILLHAM_FT = -1200


@dataclass(frozen=True)
class Altitude:
    """Numeric height together with the vertical reference it belongs to."""

    reference: str  # "barometric" | "gnss"
    unit: str       # "ft" | "m"
    value: int


def decode_altitude(frame: PositionFrame) -> Altitude:
    """Decode the altitude field of one airborne-position frame.

    Raises :class:`DecodeError` with the stable code
    ``ALTITUDE_UNAVAILABLE`` when the field is all-zero (no information),
    carries a reserved Gillham code, or otherwise cannot be interpreted.
    """
    code = frame.alt_code

    if frame.type_code in GNSS_TYPE_CODES:
        # GNSS height: raw unsigned binary, LSB = 1 m.  All zero means the
        # height information is not provided.
        if code == 0:
            raise DecodeError(
                "ALTITUDE_UNAVAILABLE",
                "GNSS altitude field is all zero: height not available",
            )
        return Altitude(reference="gnss", unit="m", value=code)

    if code == 0:
        raise DecodeError(
            "ALTITUDE_UNAVAILABLE",
            "barometric altitude field is all zero: altitude not available",
        )

    if code & 0x010:
        # 25 ft increment coding.  Removing the Q bit (ME bit 16) leaves an
        # 11-bit count N; altitude = 25 N - 1000 ft.
        n = ((code & 0x0FE0) >> 1) | (code & 0x000F)
        return Altitude(reference="barometric", unit="ft", value=25 * n - 1000)

    # 100 ft Gillham coding.  Turn the 12-bit AC field back into the 13-bit
    # form by inserting the (always zero) M bit at position 6.
    code13 = ((code & 0x0FC0) << 1) | (code & 0x003F)

    def bit(index: int) -> int:
        # 13-bit layout, bit 12..0 = C1 A1 C2 A2 C4 A4 M B1 Q B2 D2 B4 D4.
        return (code13 >> index) & 1

    # Rearrange into D2 D4 A1 A2 A4 B1 B2 B4 (500 ft Gray count) and
    # C1 C2 C4 (100 ft Gray count).
    g500 = (
        bit(2) << 7 | bit(0) << 6 | bit(11) << 5 | bit(9) << 4
        | bit(7) << 3 | bit(5) << 2 | bit(3) << 1 | bit(1)
    )
    g100 = bit(12) << 2 | bit(10) << 1 | bit(8)

    n500 = _gray_to_int(g500, 8)
    n100 = _gray_to_int(g100, 3)

    # 0, 5 and 6 are reserved; 7 is reported as 5.
    if n100 in (0, 5, 6):
        raise DecodeError(
            "ALTITUDE_UNAVAILABLE",
            f"barometric altitude field uses reserved Gillham 100 ft code {n100}",
        )
    if n100 == 7:
        n100 = 5
    if n500 & 1:
        # Odd 500 ft rows traverse the 100 ft steps in reverse order.
        n100 = 6 - n100

    value = n500 * 500 + n100 * 100 - 1300
    if value < MIN_GILLHAM_FT:
        raise DecodeError(
            "ALTITUDE_UNAVAILABLE",
            f"Gillham code decodes below the minimum representable altitude: {value} ft",
        )
    return Altitude(reference="barometric", unit="ft", value=value)


def _gray_to_int(gray: int, width: int) -> int:
    """Decode a ``width``-bit reflected (Gray) code to a plain integer."""
    value = gray
    shift = 1
    while shift < width:
        value ^= value >> shift
        shift <<= 1
    return value & ((1 << width) - 1)
