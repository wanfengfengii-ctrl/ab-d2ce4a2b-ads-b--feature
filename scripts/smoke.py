#!/usr/bin/env python3
"""Interface smoke test for the ADS-B pair decoder.

Covers: health check, a valid even/odd pair, a bad-CRC pair (stable error
code + id echo), a mixed batch proving a bad pair never shadows a good one,
and the includeAltitude flow (barometric feet, GNSS metres, and the stable
ALTITUDE_UNAVAILABLE code).  Exits 0 on success, 1 on any failure.

Usage: python scripts/smoke.py [base-url]   (default http://localhost:8000)
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Allow running as `python scripts/smoke.py` from the repo root or container.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adsb import build_position_message  # noqa: E402
from app.altitude import encode_q25  # noqa: E402

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"

# Real captured DF17 airborne-position pair, ICAO 40621D (38000 ft, Q-bit).
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
BAD_CRC = EVEN[:-1] + "0"  # EVEN ends in "7"; flipping the parity nibble breaks CRC

failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def post(payload):
    req = urllib.request.Request(
        f"{BASE}/api/adsb/positions/decode",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def main():
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=10) as resp:
            health = json.loads(resp.read())
        check("health check", health.get("status") == "ok", json.dumps(health))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        check("health check", False, f"API unreachable at {BASE}: {exc}")
        return 1

    # 1. A valid pair decodes; the newer (even, t=6000) frame wins.  Without
    #    includeAltitude the result must not gain an altitude key.
    status, body = post({"pairs": [{"id": "smoke-valid", "frames": [
        {"time_ms": 1000, "raw": ODD},
        {"time_ms": 6000, "raw": EVEN},
    ]}]})
    result = body["results"][0]
    pos = result.get("position") or {}
    ok = (
        status == 200
        and result["status"] == "ok"
        and abs(pos.get("lat", 0) - 52.257202) < 1e-6
        and abs(pos.get("lon", 0) - 3.919373) < 1e-6
        and -180.0 <= pos.get("lon", 180.0) < 180.0
        and "altitude" not in result
    )
    check("valid pair decodes to known position", ok, json.dumps(result))

    # 2. A corrupted frame is rejected with the stable CRC_MISMATCH code.
    status, body = post({"pairs": [{"id": "smoke-bad-crc", "frames": [
        {"time_ms": 1000, "raw": BAD_CRC},
        {"time_ms": 6000, "raw": ODD},
    ]}]})
    result = body["results"][0]
    ok = (
        result["id"] == "smoke-bad-crc"
        and result["status"] == "error"
        and result["error"]["code"] == "CRC_MISMATCH"
    )
    check("bad CRC rejected with stable code and id", ok, json.dumps(result))

    # 3. Mixed batch: the bad pair must not shadow the good one.
    status, body = post({"pairs": [
        {"id": "mix-bad", "frames": [
            {"time_ms": 0, "raw": BAD_CRC},
            {"time_ms": 5000, "raw": ODD},
        ]},
        {"id": "mix-good", "frames": [
            {"time_ms": 1000, "raw": ODD},
            {"time_ms": 6000, "raw": EVEN},
        ]},
    ]})
    by_id = {r["id"]: r for r in body["results"]}
    ok = (
        by_id.get("mix-bad", {}).get("status") == "error"
        and by_id["mix-bad"]["error"]["code"] == "CRC_MISMATCH"
        and by_id.get("mix-good", {}).get("status") == "ok"
    )
    check("bad pair does not shadow good pair", ok, json.dumps(body))

    # 4. includeAltitude: the real pair yields 38000 ft barometric, taken
    #    from the same newer (even) frame as the position.
    status, body = post({"includeAltitude": True, "pairs": [
        {"id": "alt-baro", "frames": [
            {"time_ms": 1000, "raw": ODD},
            {"time_ms": 6000, "raw": EVEN},
        ]},
    ]})
    result = body["results"][0]
    alt = result.get("altitude") or {}
    ok = (
        result["status"] == "ok"
        and alt.get("reference") == "barometric"
        and alt.get("unit") == "ft"
        and alt.get("value") == 38000
        and (result.get("position") or {}).get("frame") == "even"
    )
    check("includeAltitude decodes barometric feet", ok, json.dumps(result))

    # 5. includeAltitude: a GNSS pair (TC 20/22) reports unsigned metres.
    gnss_even = build_position_message(
        "ABCDEF", 52.25, 3.9, odd=False, type_code=20, altitude_field=1400
    )
    gnss_odd = build_position_message(
        "ABCDEF", 52.2505, 3.9005, odd=True, type_code=22, altitude_field=1500
    )
    status, body = post({"includeAltitude": True, "pairs": [
        {"id": "alt-gnss", "frames": [
            {"time_ms": 0, "raw": gnss_even},
            {"time_ms": 5000, "raw": gnss_odd},
        ]},
    ]})
    result = body["results"][0]
    alt = result.get("altitude") or {}
    ok = (
        result["status"] == "ok"
        and alt == {"reference": "gnss", "unit": "m", "value": 1500}
    )
    check("includeAltitude decodes GNSS metres", ok, json.dumps(result))

    # 6. An all-zero altitude field on the newer frame fails that pair with
    #    ALTITUDE_UNAVAILABLE without shadowing the healthy pair behind it.
    zero_even = build_position_message(
        "ABCDEF", 52.25, 3.9, odd=False, altitude_field=encode_q25(25000)
    )
    zero_odd = build_position_message(
        "ABCDEF", 52.2505, 3.9005, odd=True, altitude_field=0
    )
    status, body = post({"includeAltitude": True, "pairs": [
        {"id": "alt-missing", "frames": [
            {"time_ms": 0, "raw": zero_even},
            {"time_ms": 5000, "raw": zero_odd},
        ]},
        {"id": "alt-ok", "frames": [
            {"time_ms": 1000, "raw": ODD},
            {"time_ms": 6000, "raw": EVEN},
        ]},
    ]})
    by_id = {r["id"]: r for r in body["results"]}
    ok = (
        by_id.get("alt-missing", {}).get("status") == "error"
        and by_id["alt-missing"]["error"]["code"] == "ALTITUDE_UNAVAILABLE"
        and "altitude" not in by_id["alt-missing"]
        and by_id.get("alt-ok", {}).get("status") == "ok"
        and (by_id["alt-ok"].get("altitude") or {}).get("value") == 38000
    )
    check("bad altitude fails its pair only", ok, json.dumps(body))

    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
