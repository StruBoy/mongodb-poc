"""Verify residency_demo's sharded collections are correctly configured.

Atlas Global Clusters restrict sharding admin commands (`shardCollection`,
`splitChunk`, `enableSharding`, `moveChunk`) to Project Owners operating via
the Atlas UI or Atlas Administration API. No combination of database-user
roles — including atlasAdmin — grants these to an application connection.
This is by design: Atlas-Managed Sharding is the supported path for Global
Clusters and cannot be substituted with mongosh / driver calls from a regular
database user.

Pre-conditions (manual, in the Atlas UI by a Project Owner before running):
  1. Atlas Global Cluster provisioned (M30+, three zones US / EU / APAC
     mapped to us-east-1 / eu-central-1 / ap-southeast-1)
  2. Data Explorer → `residency_demo` → `customers` collection
     → Global Writes tab → Shard Collection
       - Second shard key field: `_id`
       - (Optional) Tick "Pre-split data for even distribution"
  3. Same for the `orders` collection with second shard key field `customer_id`
  4. Zone code mappings (set when prompted): US -> US, EU -> EU, APAC -> APAC

What this script does (read-only):
  - Confirms the database exists
  - Confirms both collections exist and are sharded
  - Confirms shard keys are exactly {location: 1, _id: 1} and
    {location: 1, customer_id: 1}
  - Prints the per-shard chunk distribution from config.chunks for both
    collections — this is what Atlas's zone tag ranges enforce

Run from the project root (data-residency/):
    python -m scripts.create_db_index
"""
from __future__ import annotations

import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

load_dotenv()

DB_NAME = "residency_demo"

EXPECTED = {
    "customers": {"location": 1, "_id": 1},
    "orders": {"location": 1, "customer_id": 1},
}


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  [info] {msg}")


def get_sharded_collection_doc(client, ns: str) -> dict | None:
    """Look up the config.collections entry for a namespace. None if not sharded."""
    try:
        doc = client["config"]["collections"].find_one({"_id": ns})
    except PyMongoError as e:
        info(f"could not read config.collections: {e}")
        return None
    if not doc or doc.get("dropped"):
        return None
    return doc


def report_chunk_distribution(client, ns: str) -> int:
    """Print number of chunks per shard for ns. Returns total chunk count."""
    pipeline = [
        {"$match": {"ns": ns}},
        {"$group": {"_id": "$shard", "n": {"$sum": 1}}},
    ]
    try:
        rows = list(client["config"]["chunks"].aggregate(pipeline))
    except PyMongoError as e:
        info(f"could not read config.chunks for {ns}: {e}")
        return 0
    total = sum(r["n"] for r in rows)
    print(f"    chunks for {ns}: {total} total")
    for r in sorted(rows, key=lambda x: x["_id"]):
        print(f"      - {r['_id']:30s} {r['n']} chunks")
    return total


def main() -> int:
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI not set. Add it to .env.")
        return 1

    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client[DB_NAME]
    failures = 0

    print(f"=== Verify residency_demo sharding configuration ===\n")

    # 1. Database exists
    print("1. Database")
    if DB_NAME in client.list_database_names():
        ok(f"database '{DB_NAME}' exists")
    else:
        fail(
            f"database '{DB_NAME}' does not exist. Configure Global Writes "
            f"sharding in Atlas UI to create + shard the collections."
        )
        return 2

    # 2. Collections exist
    print("\n2. Collections")
    existing = set(db.list_collection_names())
    for coll_name in EXPECTED:
        if coll_name in existing:
            ok(f"collection '{coll_name}' exists")
        else:
            fail(
                f"collection '{coll_name}' missing. Shard it via Atlas UI: "
                f"Data Explorer → {DB_NAME} → {coll_name} → Global Writes tab."
            )
            failures += 1

    if failures:
        return 2

    # 3. Sharded with the right keys
    print("\n3. Shard keys")
    for coll_name, expected_key in EXPECTED.items():
        ns = f"{DB_NAME}.{coll_name}"
        doc = get_sharded_collection_doc(client, ns)
        if not doc:
            fail(
                f"{ns} is not sharded. Use Atlas UI → Data Explorer → "
                f"{coll_name} → Global Writes tab → Shard Collection."
            )
            failures += 1
            continue
        actual_key = dict(doc.get("key", {}))
        if actual_key == expected_key:
            ok(f"{ns} sharded on {actual_key}")
        else:
            fail(
                f"{ns} sharded on {actual_key}; expected {expected_key}. "
                f"You cannot reshard a Global Cluster collection — drop the "
                f"collection in Atlas UI and re-shard with the correct key."
            )
            failures += 1

    if failures:
        return 3

    # 4. Per-shard chunk distribution
    print("\n4. Chunk distribution")
    for coll_name in EXPECTED:
        ns = f"{DB_NAME}.{coll_name}"
        total = report_chunk_distribution(client, ns)
        if total == 0:
            info(
                f"{ns} has zero chunks visible to pocuser — Atlas may not "
                f"expose config.chunks to this role. Skipping check."
            )

    print("\n=== Verification complete. Collections ready for data load. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
