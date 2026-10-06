#!/usr/bin/env python3
"""Interface smoke test for the ADS-B pair decoder.

Covers: health check, a valid even/odd pair, a bad-CRC pair (stable error
code + id echo), a mixed batch proving a bad pair never shadows a good
one, and the optional includeAltitude channel (barometric 25 ft coding,
GNSS height in metres from the newer frame, and an all-zero altitude
surfacing as ALTITUDE_UNAVAILABLE without sinking its batch neighbours).
Exits 0 on success, 1 on any failure.

Usage: python scripts/smoke.py [base-url]   (default http://localhost:8000)
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"

# Real captured DF17 airborne-position pair, ICAO 40621D, 38000 ft barometric.
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
BAD_CRC = EVEN[:-1] + "0"  # EVEN ends in "7"; flipping the parity nibble breaks CRC

# Synthetic TC=21 GNSS-height pair, ICAO ABCDEF (altitude LSB = 1 m).
GNSS_EVEN = "8DABCDEFA87D02D690C8AC59505B"  # even, 2000 m
GNSS_ODD = "8DABCDEFA84C36420EC321067DC0"   # odd,  1219 m
# TC=11 pair whose newer (even) frame carries an all-zero altitude field.
ZERO_ALT_EVEN = "8DABCDEF580002D690C8ACF85C55"
ZERO_ALT_ODD = "8DABCDEF58C386420EC321B6C6BF"  # 38000 ft, fine on its own

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

    # 1. A valid pair decodes; the newer (even, t=6000) frame wins.  With no
    # includeAltitude flag the response contract stays exactly as before.
    status, body = post({"pairs": [{"id": "smoke-valid", "frames": [
        {"time_ms": 1000, "raw": ODD},
        {"time_ms": 6000, "raw": EVEN},
    ]}]})
    result = body["results"][0]
    pos = result.get("position") or {}
    ok = (
        status == 200
        and result["status"] == "ok"
        and "altitude" not in pos
        and abs(pos.get("lat", 0) - 52.257202) < 1e-6
        and abs(pos.get("lon", 0) - 3.919373) < 1e-6
        and -180.0 <= pos.get("lon", 180.0) < 180.0
    )
    check("valid pair decodes to known position (no altitude key by default)",
          ok, json.dumps(result))

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

    # 4. includeAltitude: the captured pair reports 38000 ft barometric, taken
    # from the same newer (even) frame as the horizontal fix.
    status, body = post({"includeAltitude": True, "pairs": [
        {"id": "alt-baro", "frames": [
            {"time_ms": 1000, "raw": ODD},
            {"time_ms": 6000, "raw": EVEN},
        ]},
    ]})
    alt = (body["results"][0].get("position") or {}).get("altitude")
    ok = (
        status == 200
        and alt == {"value": 38000, "unit": "ft", "reference": "barometric"}
    )
    check("includeAltitude: 25 ft barometric height of newer frame",
          ok, json.dumps(body["results"][0]))

    # 5. includeAltitude: a TC=21 pair reports the GNSS height in metres,
    # again from the newer (odd) frame.
    status, body = post({"includeAltitude": True, "pairs": [
        {"id": "alt-gnss", "frames": [
            {"time_ms": 1000, "raw": GNSS_EVEN},
            {"time_ms": 6000, "raw": GNSS_ODD},
        ]},
    ]})
    pos = body["results"][0].get("position") or {}
    alt = pos.get("altitude") or {}
    ok = (
        status == 200
        and pos.get("frame") == "odd"
        and alt.get("reference") == "gnss"
        and alt.get("unit") == "m"
        and isinstance(alt.get("value"), int)
        and alt.get("value") == 1219
    )
    check("includeAltitude: GNSS height in metres from newer odd frame",
          ok, json.dumps(body["results"][0]))

    # 6. An all-zero altitude on the newer frame -> ALTITUDE_UNAVAILABLE for
    # that group; neighbouring groups in the batch stay healthy and ordered.
    status, body = post({"includeAltitude": True, "pairs": [
        {"id": "alt-missing", "frames": [
            {"time_ms": 6000, "raw": ZERO_ALT_EVEN},
            {"time_ms": 1000, "raw": ZERO_ALT_ODD},
        ]},
        {"id": "alt-ok", "frames": [
            {"time_ms": 1000, "raw": ODD},
            {"time_ms": 6000, "raw": EVEN},
        ]},
    ]})
    by_id = {r["id"]: r for r in body["results"]}
    missing = by_id.get("alt-missing", {})
    healthy = by_id.get("alt-ok", {})
    ok = (
        [r["id"] for r in body["results"]] == ["alt-missing", "alt-ok"]
        and missing.get("status") == "error"
        and missing.get("position") is None
        and (missing.get("error") or {}).get("code") == "ALTITUDE_UNAVAILABLE"
        and healthy.get("status") == "ok"
        and (healthy.get("position") or {}).get("altitude", {}).get("value") == 38000
    )
    check("includeAltitude: bad height reports ALTITUDE_UNAVAILABLE "
          "without shadowing other groups", ok, json.dumps(body))

    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
