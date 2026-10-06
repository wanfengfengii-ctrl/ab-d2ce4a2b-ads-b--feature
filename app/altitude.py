"""Altitude decoding for DF17 airborne-position frames.

The 12-bit altitude field (ME bits 9-20) is interpreted by type code
(RTCA DO-260 / ICAO Annex 10 Vol IV):

- TC 9-18: barometric altitude in feet.  With the Q bit set the field is
  an 11-bit linear value in 25-ft steps (offset -1000 ft); with Q clear
  it is a Gillham (modified Gray) code in 100-ft steps.
- TC 20-22: GNSS height above the ellipsoid, an unsigned 12-bit integer
  in metres.

An all-zero field carries no altitude information, and reserved or
undecodable Gillham patterns are rejected: in both cases the caller must
surface the stable ``ALTITUDE_UNAVAILABLE`` code so the fusion layer never
mixes a guessed height with a decoded horizontal position.

The ``encode_*`` helpers are the inverse operations, used to build
synthetic frames for tests and smoke checks (mirroring
:func:`app.cpr.encode_position`).
"""
from __future__ import annotations

from dataclasses import dataclass

from .adsb import PositionFrame
from .errors import DecodeError

#: Type codes whose altitude field is GNSS height (metres); 9-18 is barometric.
GNSS_HEIGHT_TCS = frozenset({20, 21, 22})

#: Q bit inside the 12-bit field: set -> 25-ft linear, clear -> Gillham.
_Q_BIT = 0x010

#: Gillham 100-ft cycle: the five valid C1C2C4 patterns per hundreds value.
_N100_TO_C_BITS = {1: 0b001, 2: 0b011, 3: 0b010, 4: 0b110, 5: 0b100}


@dataclass(frozen=True)
class Altitude:
    """Altitude of one frame with an explicit vertical reference."""

    reference: str  # "barometric" | "gnss"
    unit: str       # "ft" | "m"
    value: int


def decode_altitude(frame: PositionFrame) -> Altitude:
    """Decode the altitude field of ``frame`` according to its type code.

    Raises :class:`DecodeError` with code ``ALTITUDE_UNAVAILABLE`` when the
    field is all zero, reserved, or not decodable.
    """
    field = frame.altitude_field
    if frame.type_code in GNSS_HEIGHT_TCS:
        if field == 0:
            raise DecodeError(
                "ALTITUDE_UNAVAILABLE",
                "GNSS height field is all zeros (no altitude information)",
            )
        return Altitude(reference="gnss", unit="m", value=field)
    return Altitude(reference="barometric", unit="ft", value=_barometric_ft(field))


def _barometric_ft(field: int) -> int:
    """Decode a TC 9-18 altitude field to feet (25-ft or Gillham 100-ft)."""
    if field == 0:
        raise DecodeError(
            "ALTITUDE_UNAVAILABLE",
            "altitude field is all zeros (no altitude information)",
        )
    if field & _Q_BIT:
        # 25-ft linear encoding: N is the 11-bit value with the Q bit removed.
        n = ((field & 0xFE0) >> 1) | (field & 0x00F)
        return n * 25 - 1000
    return _gillham_ft(field)


def _gillham_ft(field: int) -> int:
    """Decode a Q=0 Gillham (Mode C) field to feet, 100-ft resolution.

    The 12-bit field is ``C1 A1 C2 A2 C4 A4 B1 Q B2 D2 B4 D4`` (MSB first);
    with Q clear the D2..B4 bits form an 8-bit Gray-coded 500-ft counter and
    the C bits a 5-state cyclic 100-ft counter.
    """
    c1 = (field >> 11) & 1
    a1 = (field >> 10) & 1
    c2 = (field >> 9) & 1
    a2 = (field >> 8) & 1
    c4 = (field >> 7) & 1
    a4 = (field >> 6) & 1
    b1 = (field >> 5) & 1
    b2 = (field >> 3) & 1
    d2 = (field >> 2) & 1
    b4 = (field >> 1) & 1
    d4 = field & 1

    n500 = _gray_to_int((d2 << 7) | (d4 << 6) | (a1 << 5) | (a2 << 4)
                        | (a4 << 3) | (b1 << 2) | (b2 << 1) | b4)
    n100 = _gray_to_int((c1 << 2) | (c2 << 1) | c4)
    # Only hundreds 1-5 exist; 0/5/6 after the Gray fold are reserved, and
    # the folded value 7 is the aliased encoding of 5.
    if n100 in (0, 5, 6):
        raise DecodeError(
            "ALTITUDE_UNAVAILABLE",
            f"Gillham altitude has reserved C-bit pattern (field 0x{field:03X})",
        )
    if n100 == 7:
        n100 = 5
    # The hundreds count runs backwards in odd 500-ft blocks.
    if n500 & 1:
        n100 = 6 - n100
    return n500 * 500 + n100 * 100 - 1300


def _gray_to_int(code: int) -> int:
    """Fold a reflected Gray code to its binary value."""
    code ^= code >> 8
    code ^= code >> 4
    code ^= code >> 2
    code ^= code >> 1
    return code


def encode_q25(alt_ft: int) -> int:
    """Encode feet into a Q=1 (25-ft resolution) 12-bit altitude field."""
    n, rem = divmod(alt_ft + 1000, 25)
    if rem or not 0 <= n <= 0x7FF:
        raise ValueError(f"altitude {alt_ft} ft is not representable in 25-ft steps")
    return ((n & 0x7F0) << 1) | _Q_BIT | (n & 0x00F)


def encode_gillham(alt_ft: int) -> int:
    """Encode feet into a Q=0 Gillham (100-ft resolution) 12-bit field."""
    total, rem = divmod(alt_ft + 1300, 100)
    if rem:
        raise ValueError(f"altitude {alt_ft} ft is not a multiple of 100 ft")
    n500, rem = divmod(total - 1, 5)
    if not 0 <= n500 <= 0xFF:
        raise ValueError(f"altitude {alt_ft} ft is outside the Gillham range")
    n100 = rem + 1
    if n500 & 1:
        n100 = 6 - n100
    g500 = n500 ^ (n500 >> 1)  # binary to reflected Gray
    c = _N100_TO_C_BITS[n100]
    return (
        ((c >> 2) & 1) << 11
        | ((g500 >> 5) & 1) << 10
        | ((c >> 1) & 1) << 9
        | ((g500 >> 4) & 1) << 8
        | (c & 1) << 7
        | ((g500 >> 3) & 1) << 6
        | ((g500 >> 2) & 1) << 5
        | ((g500 >> 1) & 1) << 3
        | ((g500 >> 7) & 1) << 2
        | (g500 & 1) << 1
        | ((g500 >> 6) & 1)
    )
