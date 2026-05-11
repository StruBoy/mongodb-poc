"""Verify the telco_demo database after running data/generate_fleet.py
(and optionally a few seconds of data/stream_telemetry.py).

Run from the project root (iot-telemetry/):
    python -m scripts.verify_data

Exits 0 on success, 1 if towers are missing, 2 on any structural failure.
The telemetry-volume check warns rather than fails when the streamer hasn't
been running — fleet integrity is the hard requirement, telemetry is a
sanity check.
"""
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from src.db import get_client, get_db

EXPECTED_FLEET_COUNT = 1000
EXPECTED_DISTRICTS = {"Central": 334, "East": 333, "West": 333}
EXPECTED_DISTRICT_PREFIXES = {"Central": "TWR-CEN-", "East": "TWR-EST-", "West": "TWR-WST-"}
EXPECTED_TOWER_TYPES = {"macro", "small_cell"}
TOWER_REQUIRED_FIELDS = {
    "_id", "region", "lat", "lon", "type", "frequency_band",
    "max_capacity_subscribers", "installed_year",
}

EXPECTED_TIMESERIES_OPTIONS = {
    "timeField": "ts",
    "metaField": "meta",
    "granularity": "seconds",
}
TELEMETRY_REQUIRED_FIELDS = {
    "ts", "meta", "signal_strength_dbm", "packet_loss_pct",
    "throughput_mbps", "temperature_c", "active_subscribers",
}
TELEMETRY_META_REQUIRED = {"tower_id", "region", "type"}


def main():
    db = get_db()
    failures: list[str] = []

    # --- 1. Tower count ---
    print("1. Tower fleet count")
    fleet_count = db.towers.count_documents({})
    if fleet_count == 0:
        print("   [FAIL] towers collection is empty. Run: python -m data.generate_fleet")
        sys.exit(1)
    marker = "OK" if fleet_count == EXPECTED_FLEET_COUNT else "WARN"
    print(f"   [{marker}] {fleet_count} towers (expected {EXPECTED_FLEET_COUNT})")

    # --- 2. District distribution ---
    print("\n2. District distribution")
    rows = list(db.towers.aggregate([
        {"$group": {"_id": "$region", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    seen = {row["_id"]: row["count"] for row in rows}
    for region, expected in EXPECTED_DISTRICTS.items():
        got = seen.get(region, 0)
        marker = "OK" if got == expected else "FAIL"
        print(f"   [{marker}] {region}: {got} (expected {expected})")
        if got != expected:
            failures.append(f"district_{region}")
    extra = set(seen) - set(EXPECTED_DISTRICTS)
    if extra:
        print(f"   [WARN] Unexpected districts: {sorted(extra)}")

    # --- 3. Tower _id prefix matches its region ---
    print("\n3. Tower ID prefix matches region")
    mismatch_count = 0
    for region, prefix in EXPECTED_DISTRICT_PREFIXES.items():
        bad = db.towers.count_documents({
            "region": region,
            "_id": {"$not": {"$regex": f"^{prefix}"}},
        })
        if bad:
            mismatch_count += bad
            print(f"   [FAIL] {region}: {bad} towers without prefix '{prefix}'")
    if mismatch_count == 0:
        print(f"   [OK] All tower IDs match their district prefix")
    else:
        failures.append("id_prefix")

    # --- 4. Tower required fields ---
    print("\n4. Tower required fields")
    sample = list(db.towers.find().limit(50))
    missing: Counter = Counter()
    for doc in sample:
        for f in TOWER_REQUIRED_FIELDS:
            if f not in doc or doc[f] is None:
                missing[f] += 1
    if missing:
        print(f"   [FAIL] Missing in sample of {len(sample)}: {dict(missing)}")
        failures.append("tower_fields")
    else:
        print(f"   [OK] All {len(TOWER_REQUIRED_FIELDS)} required fields present in {len(sample)} sampled docs")

    # --- 5. Tower types ---
    print("\n5. Tower types")
    types = {row["_id"] for row in db.towers.aggregate([
        {"$group": {"_id": "$type"}}
    ])}
    bad_types = types - EXPECTED_TOWER_TYPES
    if bad_types:
        print(f"   [FAIL] Unexpected types: {sorted(bad_types)}")
        failures.append("tower_types")
    else:
        print(f"   [OK] Types in use: {sorted(types)}")

    # --- 6. Telemetry collection is time-series with the right options ---
    print("\n6. Telemetry time-series options")
    info = list(db.list_collections(filter={"name": "telemetry"}))
    if not info:
        print("   [FAIL] telco_demo.telemetry doesn't exist. Run scripts.create_db_index.")
        failures.append("telemetry_missing")
    else:
        opts = info[0].get("options", {})
        ts_opts = opts.get("timeseries")
        if not ts_opts:
            print("   [FAIL] telemetry exists but is NOT a time-series collection.")
            failures.append("telemetry_not_timeseries")
        else:
            drift = []
            for k, expected in EXPECTED_TIMESERIES_OPTIONS.items():
                actual = ts_opts.get(k)
                if actual != expected:
                    drift.append(f"{k}: expected {expected}, got {actual}")
            if drift:
                print("   [FAIL] Time-series options drifted:")
                for line in drift:
                    print(f"     - {line}")
                failures.append("telemetry_options")
            else:
                ttl = opts.get("expireAfterSeconds")
                print(f"   [OK] timeField=ts metaField=meta granularity=seconds ttl={ttl}s")

    # --- 7. Telemetry document shape (only if we have rows) ---
    print("\n7. Telemetry document shape")
    one = db.telemetry.find_one()
    if not one:
        print("   [WARN] telemetry is empty. Run: python -m data.stream_telemetry  (in another terminal)")
    else:
        missing = TELEMETRY_REQUIRED_FIELDS - one.keys()
        if missing:
            print(f"   [FAIL] Sample doc missing fields: {sorted(missing)}")
            failures.append("telemetry_fields")
        else:
            meta_missing = TELEMETRY_META_REQUIRED - (one.get("meta") or {}).keys()
            if meta_missing:
                print(f"   [FAIL] meta missing fields: {sorted(meta_missing)}")
                failures.append("telemetry_meta")
            else:
                print(f"   [OK] All required fields + meta sub-fields present")

    # --- 8. Telemetry recency (only if we have rows) ---
    print("\n8. Telemetry recency")
    if not one:
        print("   [WARN] skipped — collection empty")
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=120)
        recent = db.telemetry.count_documents({"ts": {"$gte": cutoff}})
        if recent == 0:
            print("   [WARN] No telemetry in the last 120s — streamer is probably stopped.")
        else:
            print(f"   [OK] {recent} events in the last 120s")

    print("\nSample tower:")
    sample_tower = db.towers.find_one()
    for k, v in sorted(sample_tower.items()):
        print(f"  {k}: {v}")

    if one:
        print("\nSample telemetry event:")
        for k, v in sorted(one.items()):
            if k == "_id":
                continue
            print(f"  {k}: {v}")

    if failures:
        print(f"\nFAILED checks: {', '.join(failures)}")
        sys.exit(2)
    print("\n=== Data verification passed. ===")


if __name__ == "__main__":
    main()
