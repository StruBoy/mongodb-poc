"""Set up the telco_demo database, time-series telemetry collection, and indexes.

Performs the equivalent of plan 05 sections 1.3 and 1.4:
    1.3  use telco_demo
         db.createCollection("telemetry", {timeseries: {...}, expireAfterSeconds: 86400})
         db.createCollection("towers")
         db.createCollection("control_failures")        # extra: failure-injection state
    1.4  db.telemetry.createIndex({"meta.tower_id": 1, "ts": -1})
         db.telemetry.createIndex({"meta.region": 1, "ts": -1})
         db.towers.createIndex({"region": 1})

Idempotent — safe to re-run. Time-series collections cannot have their options
modified after creation, so we detect existing telemetry and either skip (if
options match) or fail loudly (if options have drifted).

Run from the project root (iot-telemetry/):
    python -m scripts.create_db_index
"""
import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient

load_dotenv()

DB_NAME = "telco_demo"

TELEMETRY_COLLECTION = "telemetry"
TELEMETRY_TIMESERIES_OPTIONS = {
    "timeField": "ts",
    "metaField": "meta",
    "granularity": "seconds",
}
TELEMETRY_EXPIRE_AFTER_SECONDS = 86400  # auto-delete data older than 24h

TOWERS_COLLECTION = "towers"
CONTROL_COLLECTION = "control_failures"


def get_collection_options(db, name):
    """Return the createCollection options for a collection, or None if it doesn't exist."""
    info = list(db.list_collections(filter={"name": name}))
    if not info:
        return None
    return info[0].get("options", {})


def ensure_telemetry_collection(db):
    """Create the time-series telemetry collection if missing.

    If it exists, validate that its time-series options match the plan and the
    rest of the codebase. We can't modify these options after the fact, so a
    mismatch is a hard failure — drop and recreate by hand if you really mean it.
    """
    print("=== Phase 1.3a: telemetry (time-series) ===")
    existing_options = get_collection_options(db, TELEMETRY_COLLECTION)

    if existing_options is None:
        db.create_collection(
            TELEMETRY_COLLECTION,
            timeseries=TELEMETRY_TIMESERIES_OPTIONS,
            expireAfterSeconds=TELEMETRY_EXPIRE_AFTER_SECONDS,
        )
        print(
            f"  [created] {TELEMETRY_COLLECTION} as time-series "
            f"(timeField=ts, metaField=meta, granularity=seconds, ttl=24h)"
        )
        return

    ts_opts = existing_options.get("timeseries")
    if ts_opts is None:
        print(
            f"  [FAIL] {TELEMETRY_COLLECTION} exists but is NOT a time-series collection."
        )
        print("         Drop it manually and re-run: db.telemetry.drop()")
        sys.exit(2)

    drift = []
    for key, expected in TELEMETRY_TIMESERIES_OPTIONS.items():
        actual = ts_opts.get(key)
        if actual != expected:
            drift.append(f"{key}: expected {expected!r}, got {actual!r}")

    actual_ttl = existing_options.get("expireAfterSeconds")
    if actual_ttl != TELEMETRY_EXPIRE_AFTER_SECONDS:
        drift.append(
            f"expireAfterSeconds: expected {TELEMETRY_EXPIRE_AFTER_SECONDS}, got {actual_ttl}"
        )

    if drift:
        print(f"  [FAIL] {TELEMETRY_COLLECTION} exists with mismatched options:")
        for line in drift:
            print(f"         - {line}")
        print("         Time-series options can't be modified — drop and recreate by hand.")
        sys.exit(2)

    print(f"  [skip]    {TELEMETRY_COLLECTION} already exists with correct time-series options")


def ensure_regular_collection(db, name):
    if name in db.list_collection_names():
        print(f"  [skip]    {name} already exists")
        return
    db.create_collection(name)
    print(f"  [created] {name}")


def ensure_index(collection, keys, name=None):
    spec = list(keys)
    label = name or "+".join(f"{k}({d})" for k, d in spec)
    existing = collection.index_information()
    for idx_name, info in existing.items():
        if info.get("key") == spec:
            print(f"  [skip]    {collection.name}.{label} (already exists as {idx_name!r})")
            return
    created = collection.create_index(spec, name=name) if name else collection.create_index(spec)
    print(f"  [created] {collection.name}.{label}  →  {created}")


def main():
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI not set. Add it to .env in the project root.")
        sys.exit(1)

    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client[DB_NAME]

    if DB_NAME in client.list_database_names():
        print(f"[exists]  database '{DB_NAME}'")
    else:
        print(f"[pending] database '{DB_NAME}' (will be created with the first collection)")

    ensure_telemetry_collection(db)

    print("\n=== Phase 1.3b: towers + control_failures ===")
    ensure_regular_collection(db, TOWERS_COLLECTION)
    ensure_regular_collection(db, CONTROL_COLLECTION)

    print("\n=== Phase 1.4: indexes ===")
    ensure_index(db[TELEMETRY_COLLECTION], [("meta.tower_id", ASCENDING), ("ts", DESCENDING)])
    ensure_index(db[TELEMETRY_COLLECTION], [("meta.region", ASCENDING), ("ts", DESCENDING)])
    ensure_index(db[TOWERS_COLLECTION], [("region", ASCENDING)])

    print("\nDatabase, collections, and indexes ready.")


if __name__ == "__main__":
    main()
