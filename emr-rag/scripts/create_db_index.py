"""Set up emr_demo: patients + visits collections and the kb_visits_idx vector index.

Idempotent — safe to re-run. Run from the project root (emr-rag/):
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

DB_NAME = "emr_demo"
COLLECTIONS = ["patients", "visits"]
VECTOR_INDEX_NAME = "kb_visits_idx"
VECTOR_INDEX_COLLECTION = "visits"

VECTOR_INDEX_DEFINITION = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine"},
        {"type": "filter", "path": "patient_id"},
        {"type": "filter", "path": "specialty"},
    ]
}

POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 300


def list_indexes(collection):
    return {idx["name"]: idx for idx in collection.list_search_indexes()}


def create_if_missing(collection, name, definition, index_type=None):
    if name in list_indexes(collection):
        print(f"  [skip]    {name} already exists")
        return
    kwargs = {"definition": definition, "name": name}
    if index_type:
        kwargs["type"] = index_type
    collection.create_search_index(model=SearchIndexModel(**kwargs))
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

    existing_cols = db.list_collection_names()
    for col in COLLECTIONS:
        if col in existing_cols:
            print(f"  [exists]  collection '{DB_NAME}.{col}'")
        else:
            db.create_collection(col)
            print(f"  [created] collection '{DB_NAME}.{col}'")

    return db[VECTOR_INDEX_COLLECTION]


def setup_indexes(visits_collection):
    print(f"\n=== Phase 1.4: Vector index on '{visits_collection.name}' ===")
    create_if_missing(
        visits_collection,
        VECTOR_INDEX_NAME,
        VECTOR_INDEX_DEFINITION,
        index_type="vectorSearch",
    )

    print(f"\nPolling until index is queryable (timeout {POLL_TIMEOUT_SECONDS}s)...")
    return wait_until_queryable(visits_collection, [VECTOR_INDEX_NAME])


def main():
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI not set. Add it to .env in the project root.")
        sys.exit(1)

    client = MongoClient(uri, tlsCAFile=certifi.where())
    visits = setup_database(client)
    still_pending = setup_indexes(visits)

    print("\n=== Final index status ===")
    for name, idx in list_indexes(visits).items():
        if name == VECTOR_INDEX_NAME:
            print(
                f"  {name}: type={idx.get('type', 'search')} "
                f"status={idx.get('status')} queryable={idx.get('queryable')}"
            )

    if still_pending:
        print(f"\nTimed out waiting for: {', '.join(sorted(still_pending))}")
        sys.exit(2)

    print("\nDatabase and index ready.")


if __name__ == "__main__":
    main()
