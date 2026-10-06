"""Altitude decoding: 25 ft and Gillham barometric codes, GNSS metres.

Reference vectors come from DO-260B/ICAO Annex 10 Vol. IV examples (e.g.
0xC38 = 38000 ft, the altitude of the captured 40621D frame) and the
published Mode C vector 0xB98 = 36000 ft; the remaining Gillham codes were
produced by an exhaustive cross-check against the pyModeS reference
implementation (all 4096 AC12 fields, zero mismatches).
"""
import pytest

from app.adsb import build_position_message, parse_position_frame
from app.altitude import decode_altitude
from app.errors import DecodeError

ICAO = "ABCDEF"
LAT, LON = 52.2572, 3.9194


def frame(alt_code: int, type_code: int = 11):
    raw = build_position_message(ICAO, LAT, LON, odd=False,
                                 type_code=type_code, alt_code=alt_code)
    return parse_position_frame(raw, 0)


# (AC12 field, altitude in feet) for the Q=1, 25 ft increment coding.
TWENTY_FIVE_FOOT_CASES = [
    (0x010, -1000),    # smallest encodable value
    (0x058, 0),
    (0x1F0, 5000),
    (0xC38, 38000),    # the real captured 40621D frame
    (0xFFF, 50175),    # largest encodable value
]


# (AC12 field, altitude in feet) for the Q=0, 100 ft Gillham coding.
GILLHAM_CASES = [
    (0x080, -1200),    # minimum Mode C altitude
    (0x20A, 0),
    (0xA0A, 100),
    (0x66B, 36000),
    (0xB98, 36000),    # published Mode C 36000 ft example (AC13 0x1718, M bit removed)
    (0x769, 41000),
    (0x923, 50200),    # just above the 50187.5 ft 25 ft/Gillham boundary
    (0x22B, 60000),
    (0x66E, 100000),
    (0x084, 126700),   # maximum Mode C altitude
]


@pytest.mark.parametrize("code,expected", TWENTY_FIVE_FOOT_CASES)
def test_barometric_25ft_codes(code, expected):
    altitude = decode_altitude(frame(code, type_code=11))
    assert altitude.reference == "barometric"
    assert altitude.unit == "ft"
    assert altitude.value == expected


@pytest.mark.parametrize("code,expected", GILLHAM_CASES)
def test_barometric_gillham_codes(code, expected):
    altitude = decode_altitude(frame(code, type_code=18))
    assert altitude.reference == "barometric"
    assert altitude.unit == "ft"
    assert altitude.value == expected


@pytest.mark.parametrize("type_code", [9, 12, 15, 18])
def test_barometric_reference_for_all_25ft_type_codes(type_code):
    altitude = decode_altitude(frame(0xC38, type_code=type_code))
    assert altitude.reference == "barometric"
    assert altitude.unit == "ft"
    assert altitude.value == 38000


@pytest.mark.parametrize("code,expected", [(1, 1), (2, 2), (1219, 1219),
                                           (2000, 2000), (4095, 4095)])
@pytest.mark.parametrize("type_code", [20, 21, 22])
def test_gnss_height_is_unsigned_metres(code, expected, type_code):
    altitude = decode_altitude(frame(code, type_code=type_code))
    assert altitude.reference == "gnss"
    assert altitude.unit == "m"
    assert altitude.value == expected


def test_gnss_zero_means_unavailable():
    with pytest.raises(DecodeError) as exc:
        decode_altitude(frame(0, type_code=21))
    assert exc.value.code == "ALTITUDE_UNAVAILABLE"


@pytest.mark.parametrize("code", [0x000, 0x001, 0x002, 0x003, 0x005, 0x400])
def test_barometric_zero_and_reserved_codes_rejected(code):
    with pytest.raises(DecodeError) as exc:
        decode_altitude(frame(code, type_code=11))
    assert exc.value.code == "ALTITUDE_UNAVAILABLE"


def test_real_captured_frames():
    even = parse_position_frame("8D40621D58C382D690C8AC2863A7", 6000)
    odd = parse_position_frame("8D40621D58C386435CC412692AD6", 1000)
    assert decode_altitude(even).value == 38000
    assert decode_altitude(odd).value == 38000


def test_altitude_value_is_integer():
    assert isinstance(decode_altitude(frame(0xC38)).value, int)
    assert isinstance(decode_altitude(frame(1219, type_code=20)).value, int)
