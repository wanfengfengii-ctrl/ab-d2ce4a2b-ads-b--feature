"""Frame-level checks: CRC parity, DF/type-code gating, message building."""
import pytest

from app.adsb import (
    build_message,
    build_position_message,
    crc_remainder,
    parse_position_frame,
)
from app.errors import DecodeError

# Real captured pair (ICAO 40621D) used throughout the test-suite.
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"


def corrupt(raw: str) -> str:
    """Flip the final hex digit, breaking the CRC parity field."""
    return raw[:-1] + ("0" if raw[-1] != "0" else "1")


def test_known_frames_pass_crc():
    assert crc_remainder(int(EVEN, 16), 112) == 0
    assert crc_remainder(int(ODD, 16), 112) == 0


def test_parse_position_frame_fields():
    frame = parse_position_frame(EVEN, 1234)
    assert frame.icao == "40621D"
    assert frame.type_code == 11
    assert frame.odd is False
    assert frame.time_ms == 1234
    assert frame.altitude_field == 0xC38  # 38000 ft, Q-bit set
    assert parse_position_frame(ODD, 0).odd is True


def test_corrupted_frame_fails_crc():
    with pytest.raises(DecodeError) as exc:
        parse_position_frame(corrupt(EVEN), 0)
    assert exc.value.code == "CRC_MISMATCH"


def test_wrong_length_and_non_hex():
    with pytest.raises(DecodeError) as exc:
        parse_position_frame("8D40621D", 0)
    assert exc.value.code == "INVALID_MESSAGE"
    with pytest.raises(DecodeError) as exc:
        parse_position_frame("Z" * 28, 0)
    assert exc.value.code == "INVALID_MESSAGE"


def test_non_df17_rejected():
    # Same payload as the even frame but DF=4, with a valid CRC for that DF.
    raw = build_message(df=4, icao=0x40621D, me=0x58C382D690C8AC)
    with pytest.raises(DecodeError) as exc:
        parse_position_frame(raw, 0)
    assert exc.value.code == "NOT_DF17"


def test_non_position_type_code_rejected():
    raw = build_position_message("40621D", 52.0, 4.0, odd=False, type_code=19)  # velocity
    with pytest.raises(DecodeError) as exc:
        parse_position_frame(raw, 0)
    assert exc.value.code == "NOT_AIRBORNE_POSITION"


def test_builder_roundtrip_crc_and_fields():
    raw = build_position_message("ABCDEF", 52.25, 3.9, odd=True, type_code=11)
    assert crc_remainder(int(raw, 16), 112) == 0
    frame = parse_position_frame(raw, 0)
    assert frame.icao == "ABCDEF"
    assert frame.odd is True
    assert frame.altitude_field == 0  # no altitude injected


def test_builder_injects_altitude_field():
    raw = build_position_message(
        "ABCDEF", 52.25, 3.9, odd=False, type_code=20, altitude_field=0xABC
    )
    frame = parse_position_frame(raw, 0)
    assert frame.type_code == 20
    assert frame.altitude_field == 0xABC
