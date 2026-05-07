"""Set up the citizen_demo database, collections, and Atlas Search index.

Performs the equivalent of plan 04 sections 1.3 and 1.4:
    1.3  use citizen_demo
         db.createCollection("cases")
         db.createCollection("citizens")
    1.4  create cases_search_idx — a *dynamic* Atlas Search index.

The dynamic mapping is the technical detail behind the polymorphism story:
Atlas Search indexes every field that appears in any document, regardless of
which case-type schema it came from. Adding a new case type later just works.

Idempotent — safe to re-run. Run from the project root (govtech-casemgmt/):
    python -m scripts.create_db_index
"""
import os
import sys
import time

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

load_dotenv()

DB_NAME = "citizen_demo"
COLLECTIONS = ["cases", "citizens"]
SEARCH_INDEX_NAME = "cases_search_idx"

# Dynamic mapping with a few token / date overrides for the fields we filter on.
# `dynamic: true` is the whole point — every field in every case-type schema
# becomes searchable without an explicit mapping per type.
SEARCH_INDEX_DEFINITION = {
    "mappings": {
        "dynamic": True,
        "fields": {
            "case_type": {"type": "token"},
            "status": {"type": "token"},
            "citizen_id": {"type": "token"},
            "created_at": {"type": "date"},
        },
    }
}

POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 300


def list_indexes(collection):
    return {idx["name"]: idx for idx in collection.list_search_indexes()}


def create_if_missing(collection, name, definition):
    if name in list_indexes(collection):
        print(f"  [skip]    {name} already exists")
        return
    collection.create_search_index(model=SearchIndexModel(definition=definition, name=name))
    print(f"  [created] {name}")


def wait_until_queryable(collection, names):
    pending = set(names)
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while pending and time.time() < deadline:
        indexes = list_indexes(collection)
        for name in list(pending):
            idx = indexes.get(name)
            if not idx:
                continue
            status = idx.get("status", "UNKNOWN")
            queryable = bool(idx.get("queryable", False))
            print(f"    {name}: status={status} queryable={queryable}")
            if queryable:
                pending.discard(name)
        if pending:
            time.sleep(POLL_INTERVAL_SECONDS)
    return pending


def setup_database(client):
    print("=== Phase 1.3: Database setup ===")
    db = client[DB_NAME]

    existing_dbs = client.list_database_names()
    if DB_NAME in existing_dbs:
        print(f"  [exists]  database '{DB_NAME}'")
    else:
        print(f"  [pending] database '{DB_NAME}' (will be created with the collections)")

    existing_collections = set(db.list_collection_names())
    for name in COLLECTIONS:
        if name in existing_collections:
            print(f"  [exists]  collection '{DB_NAME}.{name}'")
        else:
            db.create_collection(name)
            print(f"  [created] collection '{DB_NAME}.{name}'")

    return db


def setup_indexes(db):
    """Plan section 1.4: dynamic Atlas Search index on cases."""
    print(f"\n=== Phase 1.4: Atlas Search index on '{DB_NAME}.cases' ===")
    cases = db["cases"]
    create_if_missing(cases, SEARCH_INDEX_NAME, SEARCH_INDEX_DEFINITION)

    print(f"\nPolling until index is queryable (timeout {POLL_TIMEOUT_SECONDS}s)...")
    return wait_until_queryable(cases, [SEARCH_INDEX_NAME])


def main():
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI not set. Add it to .env in the project root.")
        sys.exit(1)

    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = setup_database(client)
    still_pending = setup_indexes(db)

    print("\n=== Final index status ===")
    for name, idx in list_indexes(db["cases"]).items():
        print(
            f"  {name}: type={idx.get('type', 'search')} "
            f"status={idx.get('status')} queryable={idx.get('queryable')}"
        )

    if still_pending:
        print(f"\nTimed out waiting for: {', '.join(sorted(still_pending))}")
        sys.exit(2)

    print("\nDatabase and search index ready.")


if __name__ == "__main__":
    main()
