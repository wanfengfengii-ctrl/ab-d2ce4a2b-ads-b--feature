"""Altitude decoding: Q-bit 25-ft, Gillham 100-ft, GNSS metres, error cases."""
import pytest

from app.adsb import build_position_message, parse_position_frame
from app.altitude import decode_altitude, encode_gillham, encode_q25
from app.errors import DecodeError

# Real captured pair (ICAO 40621D); both frames carry 38000 ft, Q-bit set.
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"


def frame_with(field, type_code=11, odd=False):
    raw = build_position_message(
        "ABCDEF", 52.25, 3.9, odd=odd, type_code=type_code, altitude_field=field
    )
    return parse_position_frame(raw, 0)


def test_real_frames_decode_38000ft_barometric():
    for raw in (EVEN, ODD):
        alt = decode_altitude(parse_position_frame(raw, 0))
        assert (alt.reference, alt.unit, alt.value) == ("barometric", "ft", 38000)


# Known-answer vectors cross-checked against a DO-260 reference decoder.
KNOWN_FIELDS = [
    (0xC38, 38000),   # Q=1 (the real frame above)
    (0xC90, 39000),   # Q=1
    (0x2D9, 8025),    # Q=1
    (0x755, 22325),   # Q=1
    (0xB14, 34300),   # Q=1
    (0x20A, 0),       # Gillham 100-ft
    (0x0A8, 800),     # Gillham 100-ft
    (0x5A2, 17800),   # Gillham 100-ft
    (0x1C6, 117800),  # Gillham 100-ft, high block
]


@pytest.mark.parametrize("field,expect_ft", KNOWN_FIELDS)
def test_known_altitude_fields(field, expect_ft):
    alt = decode_altitude(frame_with(field))
    assert (alt.reference, alt.unit, alt.value) == ("barometric", "ft", expect_ft)


@pytest.mark.parametrize("alt_ft", [-1000, -975, 0, 1000, 38000, 50175])
def test_q25_roundtrip(alt_ft):
    assert decode_altitude(frame_with(encode_q25(alt_ft))).value == alt_ft


@pytest.mark.parametrize(
    "alt_ft", [-1200, -1000, 0, 100, 500, 1300, 10000, 31500, 126700]
)
def test_gillham_roundtrip(alt_ft):
    assert decode_altitude(frame_with(encode_gillham(alt_ft))).value == alt_ft


def test_q25_encoding_boundaries():
    assert decode_altitude(frame_with(0x010)).value == -1000   # N = 0
    assert decode_altitude(frame_with(0xFFF)).value == 50175   # N = 2047


def test_all_zero_field_unavailable():
    with pytest.raises(DecodeError) as exc:
        decode_altitude(frame_with(0x000))
    assert exc.value.code == "ALTITUDE_UNAVAILABLE"


@pytest.mark.parametrize("field", [0x008, 0x880, 0xA80])
def test_reserved_gillham_patterns_unavailable(field):
    # 0x008: C bits all zero; 0x880 / 0xA80: reserved C-bit patterns.
    with pytest.raises(DecodeError) as exc:
        decode_altitude(frame_with(field))
    assert exc.value.code == "ALTITUDE_UNAVAILABLE"


@pytest.mark.parametrize("type_code", [20, 21, 22])
def test_gnss_height_unsigned_metres(type_code):
    alt = decode_altitude(frame_with(1500, type_code=type_code))
    assert (alt.reference, alt.unit, alt.value) == ("gnss", "m", 1500)


def test_gnss_height_full_unsigned_range():
    assert decode_altitude(frame_with(4095, type_code=20)).value == 4095


def test_gnss_all_zero_unavailable():
    with pytest.raises(DecodeError) as exc:
        decode_altitude(frame_with(0, type_code=20))
    assert exc.value.code == "ALTITUDE_UNAVAILABLE"
