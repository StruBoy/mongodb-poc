"""Set up the kb_demo database, documents collection, and vector search index.

Performs the equivalent of plan 03 sections 1.3 and 1.4:
    1.3  use kb_demo
         db.createCollection("documents")
    1.4  create kb_vector_idx (Atlas Vector Search on documents.embedding)

Idempotent — safe to re-run. Run from the project root (ai-kb/):
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

DB_NAME = "kb_demo"
COLLECTION_NAME = "documents"
VECTOR_INDEX_NAME = "kb_vector_idx"

VECTOR_INDEX_DEFINITION = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine"},
        {"type": "filter", "path": "category"},
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
    """Plan section 1.3: ensure the database and documents collection exist.

    MongoDB creates databases lazily on first write, so 'use kb_demo' alone
    is a no-op here — we rely on collection creation to materialize the db.
    """
    print("=== Phase 1.3: Database setup ===")
    db = client[DB_NAME]

    existing_dbs = client.list_database_names()
    if DB_NAME in existing_dbs:
        print(f"  [exists]  database '{DB_NAME}'")
    else:
        print(f"  [pending] database '{DB_NAME}' (will be created with the collection)")

    if COLLECTION_NAME in db.list_collection_names():
        print(f"  [exists]  collection '{DB_NAME}.{COLLECTION_NAME}'")
    else:
        db.create_collection(COLLECTION_NAME)
        print(f"  [created] collection '{DB_NAME}.{COLLECTION_NAME}'")

    return db[COLLECTION_NAME]


def setup_indexes(collection):
    """Plan section 1.4: create the Vector Search index on documents."""
    print(f"\n=== Phase 1.4: Index creation on '{collection.name}' ===")
    create_if_missing(collection, VECTOR_INDEX_NAME, VECTOR_INDEX_DEFINITION, index_type="vectorSearch")

    print(f"\nPolling until index is queryable (timeout {POLL_TIMEOUT_SECONDS}s)...")
    return wait_until_queryable(collection, [VECTOR_INDEX_NAME])


def main():
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI not set. Add it to .env in the project root.")
        sys.exit(1)

    client = MongoClient(uri, tlsCAFile=certifi.where())
    collection = setup_database(client)
    still_pending = setup_indexes(collection)

    print("\n=== Final index status ===")
    for name, idx in list_indexes(collection).items():
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
