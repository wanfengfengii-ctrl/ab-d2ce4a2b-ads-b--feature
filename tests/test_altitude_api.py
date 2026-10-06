"""Endpoint behaviour of includeAltitude: per-pair vertical reference."""
from fastapi.testclient import TestClient

from app.adsb import build_position_message
from app.altitude import encode_gillham, encode_q25
from app.main import app

URL = "/api/adsb/positions/decode"
EVEN = "8D40621D58C382D690C8AC2863A7"  # real capture, 38000 ft Q-bit
ODD = "8D40621D58C386435CC412692AD6"
BAD_CRC = EVEN[:-1] + "0"

client = TestClient(app)


def frame(raw, time_ms):
    return {"time_ms": time_ms, "raw": raw}


def post(pairs, **extra):
    payload = {"pairs": pairs}
    payload.update(extra)
    return client.post(URL, json=payload)


def synthetic(altitude_field, odd, type_code=11, icao="ABCDEF", lat=52.25, lon=3.9):
    return build_position_message(
        icao, lat, lon, odd=odd, type_code=type_code, altitude_field=altitude_field
    )


def test_flag_omitted_or_false_keeps_contract_unchanged():
    pairs = [{"id": "p1", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}]
    omitted = post(pairs).json()["results"][0]
    disabled = post(pairs, includeAltitude=False).json()["results"][0]
    assert omitted == disabled
    assert "altitude" not in omitted
    assert omitted == {
        "id": "p1",
        "status": "ok",
        "position": {
            "lat": 52.257202,
            "lon": 3.919373,
            "time_ms": 6_000,
            "icao": "40621D",
            "frame": "even",
        },
        "error": None,
    }


def test_include_altitude_barometric_real_pair():
    pairs = [{"id": "p1", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["status"] == "ok"
    assert result["altitude"] == {
        "reference": "barometric",
        "unit": "ft",
        "value": 38000,
    }
    # The horizontal result is untouched by the extra decoding.
    assert result["position"]["lat"] == 52.257202
    assert result["position"]["frame"] == "even"


def test_altitude_comes_from_the_newer_frame_only():
    even = synthetic(encode_q25(10_000), odd=False)
    odd = synthetic(encode_q25(20_000), odd=True, lat=52.2505, lon=3.9005)
    # Odd newer -> odd's altitude, even though the even frame's is valid too.
    pairs = [{"id": "odd-newer", "frames": [frame(even, 0), frame(odd, 5_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["position"]["frame"] == "odd"
    assert result["altitude"]["value"] == 20_000
    # Even newer -> even's altitude; the odd frame's zero field is irrelevant.
    odd_zero = synthetic(0, odd=True, lat=52.2505, lon=3.9005)
    pairs = [{"id": "even-newer", "frames": [frame(even, 9_000), frame(odd_zero, 1_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["status"] == "ok"
    assert result["position"]["frame"] == "even"
    assert result["altitude"]["value"] == 10_000


def test_equal_receive_time_matches_even_frame_position():
    even = synthetic(encode_q25(10_000), odd=False)
    odd = synthetic(encode_q25(20_000), odd=True, lat=52.2505, lon=3.9005)
    pairs = [{"id": "tie", "frames": [frame(even, 5_000), frame(odd, 5_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["status"] == "ok"
    assert result["position"]["frame"] == "even"
    assert result["position"]["time_ms"] == 5_000
    assert result["altitude"] == {
        "reference": "barometric",
        "unit": "ft",
        "value": 10_000,
    }


def test_gillham_hundred_feet_encoding():
    even = synthetic(encode_q25(30_000), odd=False)
    odd = synthetic(encode_gillham(10_000), odd=True, lat=52.2505, lon=3.9005)
    pairs = [{"id": "gillham", "frames": [frame(even, 0), frame(odd, 5_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["altitude"] == {
        "reference": "barometric",
        "unit": "ft",
        "value": 10_000,
    }


def test_gnss_height_pair_reports_metres():
    even = synthetic(1_400, odd=False, type_code=20)
    odd = synthetic(1_500, odd=True, type_code=22, lat=52.2505, lon=3.9005)
    pairs = [{"id": "gnss", "frames": [frame(even, 0), frame(odd, 5_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["status"] == "ok"
    assert result["altitude"] == {"reference": "gnss", "unit": "m", "value": 1_500}


def test_unusable_altitude_fails_pair_with_stable_code():
    even = synthetic(encode_q25(10_000), odd=False)
    odd_zero = synthetic(0, odd=True, lat=52.2505, lon=3.9005)
    pairs = [{"id": "no-alt", "frames": [frame(even, 0), frame(odd_zero, 5_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["status"] == "error"
    assert result["error"]["code"] == "ALTITUDE_UNAVAILABLE"
    assert result["position"] is None
    assert "altitude" not in result
    # The same pair decodes fine when altitude was never requested.
    (plain,) = post(pairs).json()["results"]
    assert plain["status"] == "ok"
    assert "altitude" not in plain


def test_bad_altitude_does_not_shadow_other_pairs():
    good = synthetic(encode_q25(25_000), odd=False)
    good_odd = synthetic(encode_q25(25_000), odd=True, lat=52.2505, lon=3.9005)
    zero_odd = synthetic(0, odd=True, lat=52.2505, lon=3.9005)
    pairs = [
        {"id": "bad-alt", "frames": [frame(good, 0), frame(zero_odd, 5_000)]},
        {"id": "good", "frames": [frame(good, 0), frame(good_odd, 5_000)]},
        {"id": "bad-crc", "frames": [frame(BAD_CRC, 0), frame(ODD, 5_000)]},
    ]
    resp = post(pairs, includeAltitude=True)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["id"] for r in results] == ["bad-alt", "good", "bad-crc"]
    assert results[0]["error"]["code"] == "ALTITUDE_UNAVAILABLE"
    assert results[1]["status"] == "ok"
    assert results[1]["altitude"]["value"] == 25_000
    assert results[2]["error"]["code"] == "CRC_MISMATCH"


def test_error_pair_never_carries_altitude():
    pairs = [{"id": "broken", "frames": [frame(BAD_CRC, 0), frame(ODD, 5_000)]}]
    (result,) = post(pairs, includeAltitude=True).json()["results"]
    assert result["status"] == "error"
    assert result["error"]["code"] == "CRC_MISMATCH"
    assert "altitude" not in result


def test_batch_limits_still_enforced_with_flag():
    pairs = [
        {"id": f"p{i}", "frames": [frame(EVEN, 0), frame(ODD, 5_000)]}
        for i in range(201)
    ]
    assert post(pairs, includeAltitude=True).status_code == 422


def test_flag_name_is_exact_and_unknown_fields_rejected():
    pairs = [{"id": "p1", "frames": [frame(EVEN, 0), frame(ODD, 5_000)]}]
    assert post(pairs, include_altitude=True).status_code == 422
    assert post(pairs, includeAltitude="definitely").status_code == 422
