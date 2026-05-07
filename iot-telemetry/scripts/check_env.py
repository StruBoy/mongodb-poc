"""Verify the local environment is configured correctly for the iot-telemetry PoC.

Checks:
  1. .env loads and required variables are present
  2. The MongoDB URI authenticates and the cluster responds to a ping
  3. The telco_demo database is reachable (collections may not yet exist)

No Voyage / Anthropic dependencies — this PoC is pure time-series + aggregations.

Run from the project root (iot-telemetry/):
    python -m scripts.check_env
"""
import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError

load_dotenv()

DB_NAME = "telco_demo"
EXPECTED_COLLECTIONS = ["towers", "telemetry", "control_failures"]

REQUIRED_VARS = ["MONGODB_URI"]


def check_env_vars():
    print("1. Environment variables")
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    for v in REQUIRED_VARS:
        status = "OK" if os.environ.get(v) else "MISSING"
        print(f"   [{status}] {v}")
    return not missing


def check_mongo_ping(uri):
    print("\n2. MongoDB connection")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000, tlsCAFile=certifi.where())
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


def check_database(client):
    print(f"\n3. Database '{DB_NAME}'")
    try:
        existing_dbs = client.list_database_names()
    except PyMongoError as e:
        print(f"   [FAIL] Cannot list databases: {e}")
        return False
    if DB_NAME in existing_dbs:
        print(f"   [OK] Database exists.")
    else:
        print(f"   [info] Database not yet created (run scripts.create_db_index next).")

    db = client[DB_NAME]
    try:
        collections = db.list_collection_names()
    except PyMongoError as e:
        print(f"   [FAIL] Cannot list collections: {e}")
        return False

    for name in EXPECTED_COLLECTIONS:
        if name in collections:
            count = db[name].estimated_document_count()
            print(f"   [OK] Collection '{name}' exists with ~{count} documents.")
        else:
            print(f"   [info] Collection '{name}' not yet created.")
    return True


def main():
    print("=== iot-telemetry environment check ===\n")

    if not check_env_vars():
        print("\nFAILED: required environment variables are missing.")
        sys.exit(1)

    client = check_mongo_ping(os.environ["MONGODB_URI"])
    if client is None:
        sys.exit(2)

    if not check_database(client):
        sys.exit(3)

    print("\n=== All checks passed. Environment is ready. ===")


if __name__ == "__main__":
    main()
