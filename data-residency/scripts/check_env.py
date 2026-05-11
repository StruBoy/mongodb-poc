"""Verify the local environment is configured correctly for the data-residency PoC.

Checks:
  1. .env loads and MONGODB_URI is present
  2. MongoDB URI authenticates and the cluster responds to a ping
  3. The cluster is sharded (mongos visible, listShards returns >= 3 shards)
  4. The expected zone shards exist (one per US / EU / APAC AWS region)
  5. residency_demo database is reachable and shows current state of customers / orders

Run from the project root (data-residency/):
    python -m scripts.check_env
"""
import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError

load_dotenv()

DB_NAME = "residency_demo"
COLLECTIONS = ["customers", "orders"]
# One region tag we expect to see somewhere in the listShards host strings
# per zone. Multi-cloud: US shard on AWS, EU on Azure, APAC on GCP. Atlas
# hostnames sometimes embed the region tag (e.g. `*.gcp.mongodb.net`,
# `*.azure.mongodb.net`); this check is best-effort and warns rather than
# fails if the tag isn't present.
EXPECTED_REGIONS = {"us-east-1", "germanywestcentral", "asia-southeast1"}


def check_env_vars():
    print("1. Environment variables")
    if not os.environ.get("MONGODB_URI"):
        print("   [FAIL] MONGODB_URI not set in .env")
        return False
    print("   [OK] MONGODB_URI present")
    return True


def check_mongo_ping(uri):
    print("\n2. MongoDB connection")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=8000, tlsCAFile=certifi.where())
        client.admin.command("ping")
    except ConfigurationError as e:
        print(f"   [FAIL] URI is malformed: {e}")
        return None
    except OperationFailure as e:
        print(f"   [FAIL] Authentication failed: {e}")
        return None
    except PyMongoError as e:
        print(f"   [FAIL] Could not reach cluster: {e}")
        return None
    server_info = client.server_info()
    print(f"   [OK] Connected. Server version: {server_info.get('version')}")
    return client


def check_sharded_cluster(client):
    print("\n3. Sharded cluster topology")
    try:
        is_master = client.admin.command("hello")
    except PyMongoError as e:
        print(f"   [FAIL] hello command failed: {e}")
        return None

    msg = is_master.get("msg")
    if msg != "isdbgrid":
        print(
            f"   [FAIL] Connected node is not a mongos router (msg={msg!r}). "
            f"This PoC requires a sharded Atlas Global Cluster."
        )
        return None
    print("   [OK] Connected through mongos (sharded cluster)")

    try:
        shards_resp = client.admin.command("listShards")
    except OperationFailure as e:
        print(
            f"   [FAIL] listShards denied: {e}. The pocuser needs the "
            f"`clusterMonitor` role in addition to `readWriteAnyDatabase`."
        )
        return None
    except PyMongoError as e:
        print(f"   [FAIL] listShards command failed: {e}")
        return None

    shards = shards_resp.get("shards", [])
    print(f"   [OK] Cluster has {len(shards)} shard(s):")
    for s in shards:
        print(f"        - {s.get('_id')}  →  {s.get('host', '')[:80]}...")
    if len(shards) < 3:
        print(
            f"   [FAIL] Expected at least 3 shards (one per zone). "
            f"Confirm Atlas Global Cluster has US / EU / APAC zones configured."
        )
        return None
    return shards


def check_zone_regions(shards):
    print("\n4. Zone regions")
    if not shards:
        print("   [SKIP] no shards returned")
        return False
    found_regions = set()
    for s in shards:
        host = s.get("host", "")
        for region in EXPECTED_REGIONS:
            if region in host:
                found_regions.add(region)
                break
    missing = EXPECTED_REGIONS - found_regions
    if missing:
        print(f"   [WARN] Did not detect shards in regions: {sorted(missing)}")
        print(f"          Found: {sorted(found_regions)}")
        print(
            "          The host strings may not contain region tags on every Atlas tier; "
            "if you've manually verified the zones in the Atlas UI, this warning is harmless."
        )
        return True  # warn only — Atlas hostnames don't always include region tags
    print(f"   [OK] All three expected regions present: {sorted(found_regions)}")
    return True


def check_database(client):
    print(f"\n5. Database '{DB_NAME}'")
    try:
        existing_dbs = client.list_database_names()
    except PyMongoError as e:
        print(f"   [FAIL] Cannot list databases: {e}")
        return False

    if DB_NAME in existing_dbs:
        print(f"   [OK] Database exists.")
    else:
        print(f"   [info] Database not yet created (will be created on first insert).")

    db = client[DB_NAME]
    try:
        collections = db.list_collection_names()
    except PyMongoError as e:
        print(f"   [FAIL] Cannot list collections: {e}")
        return False

    for name in COLLECTIONS:
        if name in collections:
            count = db[name].estimated_document_count()
            print(f"   [OK] Collection '{name}' exists with ~{count:,} documents.")
        else:
            print(f"   [info] Collection '{name}' not yet created.")
    return True


def main():
    print("=== data-residency environment check ===\n")

    if not check_env_vars():
        sys.exit(1)

    client = check_mongo_ping(os.environ["MONGODB_URI"])
    if client is None:
        sys.exit(2)

    shards = check_sharded_cluster(client)
    if shards is None:
        sys.exit(3)

    if not check_zone_regions(shards):
        sys.exit(4)

    if not check_database(client):
        sys.exit(5)

    print("\n=== All checks passed. Environment is ready. ===")


if __name__ == "__main__":
    main()
