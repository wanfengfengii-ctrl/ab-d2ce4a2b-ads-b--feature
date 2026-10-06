"""Endpoint behaviour: per-pair verdicts, stable error codes, batch limits."""
import pytest
from fastapi.testclient import TestClient

from app.adsb import build_position_message
from app.main import app

URL = "/api/adsb/positions/decode"
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
BAD_CRC = EVEN[:-1] + "0"  # EVEN ends in "7"; flipping it breaks parity

# Synthetic TC=21 GNSS-height pair, ICAO ABCDEF (altitude LSB = 1 m).
GNSS_EVEN = build_position_message("ABCDEF", 52.2572, 3.9194, odd=False,
                                   type_code=21, alt_code=2000)
GNSS_ODD = build_position_message("ABCDEF", 52.2580, 3.9200, odd=True,
                                  type_code=21, alt_code=1219)
# Same ICAO but barometric (TC=11) counterparts of the two frames above.
BARO_EVEN = build_position_message("ABCDEF", 52.2572, 3.9194, odd=False,
                                   type_code=11, alt_code=0xC38)
BARO_ODD = build_position_message("ABCDEF", 52.2580, 3.9200, odd=True,
                                  type_code=11, alt_code=0xC38)
# TC=11 even frame whose altitude field is all zero (height not available).
ZERO_ALT_EVEN = build_position_message("ABCDEF", 52.2572, 3.9194, odd=False,
                                       type_code=11, alt_code=0)
ZERO_ALT_ODD = build_position_message("ABCDEF", 52.2580, 3.9200, odd=True,
                                      type_code=11, alt_code=0xC38)

client = TestClient(app)


def frame(raw, time_ms):
    return {"time_ms": time_ms, "raw": raw}


def post(pairs, include_altitude=None):
    payload = {"pairs": pairs}
    if include_altitude is not None:
        payload["includeAltitude"] = include_altitude
    return client.post(URL, json=payload)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_valid_pair_decodes_newer_frame():
    resp = post([{"id": "p1", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}])
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["id"] == "p1"
    assert result["status"] == "ok"
    pos = result["position"]
    assert pos["lat"] == 52.257202
    assert pos["lon"] == 3.919373
    assert pos["frame"] == "even"
    assert pos["time_ms"] == 6_000
    assert pos["icao"] == "40621D"
    assert result["error"] is None


def test_bad_crc_reports_stable_code_and_id():
    resp = post([{"id": "broken-1", "frames": [frame(BAD_CRC, 0), frame(ODD, 5_000)]}])
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["id"] == "broken-1"
    assert result["status"] == "error"
    assert result["error"]["code"] == "CRC_MISMATCH"
    assert result["position"] is None


def test_bad_pair_does_not_shadow_good_pair():
    pairs = [
        {"id": "bad-crc", "frames": [frame(BAD_CRC, 0), frame(ODD, 5_000)]},
        {"id": "good", "frames": [frame(EVEN, 6_000), frame(ODD, 1_000)]},
        {"id": "bad-gap", "frames": [frame(EVEN, 0), frame(ODD, 30_000)]},
    ]
    resp = post(pairs)
    assert resp.status_code == 200
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["bad-crc"]["error"]["code"] == "CRC_MISMATCH"
    assert results["bad-gap"]["error"]["code"] == "TIME_GAP_EXCEEDED"
    assert results["good"]["status"] == "ok"
    assert results["good"]["position"]["lat"] == 52.257202


def test_icao_mismatch():
    other = build_position_message("ABCDEF", 52.26, 3.92, odd=True)
    resp = post([{"id": "x", "frames": [frame(EVEN, 0), frame(other, 5_000)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "ICAO_MISMATCH"


def test_same_cpr_flag_rejected():
    resp = post([{"id": "x", "frames": [frame(EVEN, 0), frame(EVEN, 5_000)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "SAME_CPR_FLAG"


def test_time_gap_boundary():
    # Exactly 10 s apart is still acceptable...
    resp = post([{"id": "edge", "frames": [frame(EVEN, 0), frame(ODD, 10_000)]}])
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    # ...10 s + 1 ms is not.
    resp = post([{"id": "over", "frames": [frame(EVEN, 0), frame(ODD, 10_001)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "TIME_GAP_EXCEEDED"


def test_not_df17_and_not_position_codes():
    from app.adsb import build_message
    df4 = build_message(df=4, icao=0x40621D, me=0x58C382D690C8AC)
    velocity = build_position_message("40621D", 52.0, 4.0, odd=True, type_code=19)
    resp = post([
        {"id": "df4", "frames": [frame(df4, 0), frame(ODD, 5_000)]},
        {"id": "vel", "frames": [frame(EVEN, 0), frame(velocity, 5_000)]},
    ])
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["df4"]["error"]["code"] == "NOT_DF17"
    assert results["vel"]["error"]["code"] == "NOT_AIRBORNE_POSITION"


def test_latitude_zone_mismatch_surfaces_as_pair_error():
    even = build_position_message("40621D", 36.84, 10.0, odd=False)
    odd = build_position_message("40621D", 36.86, 10.0, odd=True)
    resp = post([{"id": "zones", "frames": [frame(even, 0), frame(odd, 5_000)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "LATITUDE_ZONE_MISMATCH"


def test_batch_size_limits():
    assert post([]).status_code == 422
    pairs = [{"id": f"p{i}", "frames": [frame(EVEN, 0), frame(ODD, 5_000)]} for i in range(201)]
    assert post(pairs).status_code == 422
    pairs = pairs[:200]
    assert post(pairs).status_code == 200


def test_duplicate_ids_rejected():
    pair = {"id": "dup", "frames": [frame(EVEN, 0), frame(ODD, 5_000)]}
    assert post([pair, pair]).status_code == 422


def test_wrong_frame_count_rejected():
    assert post([{"id": "x", "frames": [frame(EVEN, 0)]}]).status_code == 422
    three = [frame(EVEN, 0), frame(ODD, 5_000), frame(EVEN, 6_000)]
    assert post([{"id": "x", "frames": three}]).status_code == 422


def test_numeric_ids_accepted_and_echoed():
    resp = post([{"id": 7, "frames": [frame(EVEN, 6_000), frame(ODD, 1_000)]}])
    assert resp.status_code == 200
    assert resp.json()["results"][0]["id"] == "7"


# --- includeAltitude -------------------------------------------------------

def test_altitude_omitted_by_default_and_when_false():
    resp = post([{"id": "p1", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}])
    assert resp.status_code == 200
    assert "altitude" not in resp.json()["results"][0]["position"]

    resp = post([{"id": "p1", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}],
                include_altitude=False)
    assert resp.status_code == 200
    assert "altitude" not in resp.json()["results"][0]["position"]


def test_barometric_altitude_of_newer_frame():
    resp = post([{"id": "p1", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}],
                include_altitude=True)
    pos = resp.json()["results"][0]["position"]
    assert pos["frame"] == "even"
    assert pos["altitude"] == {
        "value": 38000,
        "unit": "ft",
        "reference": "barometric",
    }
    assert pos["time_ms"] == 6_000


def test_gnss_height_of_odd_newer_frame():
    resp = post([{"id": "gnss", "frames": [frame(GNSS_EVEN, 1_000),
                                           frame(GNSS_ODD, 6_000)]}],
                include_altitude=True)
    result = resp.json()["results"][0]
    assert result["status"] == "ok"
    pos = result["position"]
    assert pos["frame"] == "odd"
    assert pos["time_ms"] == 6_000
    assert pos["altitude"] == {"value": 1219, "unit": "m", "reference": "gnss"}


def test_equal_timestamps_tie_altitude_to_even_frame():
    # The horizontal fix belongs to the even frame on a receive-time tie, and
    # the height must come from that same even frame (2000 m), not the odd one.
    resp = post([{"id": "tie", "frames": [frame(GNSS_EVEN, 5_000),
                                          frame(GNSS_ODD, 5_000)]}],
                include_altitude=True)
    pos = resp.json()["results"][0]["position"]
    assert pos["frame"] == "even"
    assert pos["time_ms"] == 5_000
    assert pos["altitude"]["value"] == 2000
    assert pos["altitude"]["reference"] == "gnss"


def test_unavailable_altitude_on_newer_frame_is_group_error():
    resp = post([{"id": "no-alt", "frames": [frame(ZERO_ALT_EVEN, 6_000),
                                             frame(ZERO_ALT_ODD, 1_000)]}],
                include_altitude=True)
    result = resp.json()["results"][0]
    assert result["status"] == "error"
    assert result["position"] is None
    assert result["error"]["code"] == "ALTITUDE_UNAVAILABLE"


def test_zero_altitude_on_older_frame_does_not_matter():
    # The all-zero frame is older; the newer odd frame carries a good height.
    resp = post([{"id": "older-zero", "frames": [frame(ZERO_ALT_EVEN, 1_000),
                                                 frame(ZERO_ALT_ODD, 6_000)]}],
                include_altitude=True)
    pos = resp.json()["results"][0]["position"]
    assert pos["frame"] == "odd"
    assert pos["altitude"] == {"value": 38000, "unit": "ft",
                               "reference": "barometric"}


def test_mixed_references_always_follow_the_newer_frame():
    # Even frame barometric, odd frame GNSS: the newer one dictates the datum.
    resp = post([{"id": "odd-newer", "frames": [frame(BARO_EVEN, 1_000),
                                                frame(GNSS_ODD, 6_000)]}],
                include_altitude=True)
    pos = resp.json()["results"][0]["position"]
    assert pos["frame"] == "odd"
    assert pos["altitude"] == {"value": 1219, "unit": "m", "reference": "gnss"}

    # GNSS even newer + barometric odd older: the result is still GNSS, and
    # its value comes from the even frame rather than the odd one.
    resp = post([{"id": "even-newer", "frames": [frame(GNSS_EVEN, 6_000),
                                                 frame(BARO_ODD, 1_000)]}],
                include_altitude=True)
    pos = resp.json()["results"][0]["position"]
    assert pos["frame"] == "even"
    assert pos["altitude"] == {"value": 2000, "unit": "m", "reference": "gnss"}


def test_bad_altitude_does_not_shadow_other_groups_and_keeps_order():
    pairs = [
        {"id": "good-gnss", "frames": [frame(GNSS_EVEN, 1_000),
                                       frame(GNSS_ODD, 6_000)]},
        {"id": "bad-alt", "frames": [frame(ZERO_ALT_EVEN, 6_000),
                                     frame(ZERO_ALT_ODD, 1_000)]},
        {"id": "good-baro", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]},
    ]
    resp = post(pairs, include_altitude=True)
    results = resp.json()["results"]
    assert [r["id"] for r in results] == ["good-gnss", "bad-alt", "good-baro"]
    assert results[0]["position"]["altitude"]["reference"] == "gnss"
    assert results[1]["error"]["code"] == "ALTITUDE_UNAVAILABLE"
    assert results[2]["position"]["altitude"]["reference"] == "barometric"


def test_altitude_unavailable_suppressed_without_flag():
    # includeAltitude omitted: the undecodable height must not sink the group.
    resp = post([{"id": "still-ok", "frames": [frame(ZERO_ALT_EVEN, 6_000),
                                               frame(ZERO_ALT_ODD, 1_000)]}])
    result = resp.json()["results"][0]
    assert result["status"] == "ok"
    assert "altitude" not in result["position"]


def test_unknown_request_field_still_rejected():
    resp = client.post(URL, json={"includeAltitude": True, "bogus": 1,
                                  "pairs": []})
    assert resp.status_code == 422


def test_include_altitude_must_be_boolean():
    resp = client.post(URL, json={"includeAltitude": "maybe",
                                  "pairs": [{"id": "p1",
                                             "frames": [frame(ODD, 0),
                                                        frame(EVEN, 5_000)]}]})
    assert resp.status_code == 422
