"""Verify the local environment is configured correctly for the hybrid-search PoC.

Checks:
  1. .env loads and required variables are present
  2. The MongoDB URI authenticates and the cluster responds to a ping
  3. The target database is reachable and its collections can be listed
  4. Voyage AI accepts the API key and returns an embedding
  5. Voyage AI rate limit is above the free tier (i.e. payment method is configured)
  6. Anthropic accepts the API key and returns a model list

Run from the project root:
    python -m scripts.check_env
"""
import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError

load_dotenv()

DB_NAME = "catalog_demo"
COLLECTION_NAME = "products"

REQUIRED_VARS = ["MONGODB_URI"]
OPTIONAL_VARS = ["VOYAGE_API_KEY", "ANTHROPIC_API_KEY"]


def check_env_vars():
    print("1. Environment variables")
    missing_required = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    for v in REQUIRED_VARS:
        status = "OK" if os.environ.get(v) else "MISSING"
        print(f"   [{status}] {v}")
    for v in OPTIONAL_VARS:
        status = "OK" if os.environ.get(v) else "not set"
        print(f"   [{status}] {v} (needed later for embeddings/Claude)")
    return not missing_required


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
        print(f"   [info] Database not yet created (will be created on first insert).")

    db = client[DB_NAME]
    try:
        collections = db.list_collection_names()
    except PyMongoError as e:
        print(f"   [FAIL] Cannot list collections: {e}")
        return False

    if COLLECTION_NAME in collections:
        count = db[COLLECTION_NAME].estimated_document_count()
        print(f"   [OK] Collection '{COLLECTION_NAME}' exists with ~{count} documents.")
    else:
        print(f"   [info] Collection '{COLLECTION_NAME}' not yet created.")
    return True


def check_voyage():
    print("\n4. Voyage AI")
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print("   [skip] VOYAGE_API_KEY not set")
        return True
    try:
        import voyageai
    except ImportError:
        print("   [FAIL] voyageai package not installed. Run: pip install voyageai")
        return False
    try:
        client = voyageai.Client(api_key=api_key)
        result = client.embed(["ping"], model="voyage-3", input_type="query")
    except Exception as e:
        print(f"   [FAIL] Embedding call failed: {e}")
        return False
    dims = len(result.embeddings[0])
    print(f"   [OK] Authenticated. voyage-3 returned {dims}-dim vector.")
    return True


def check_voyage_billing():
    """Probe Voyage's rate limit. Free tier = 3 RPM; 4 rapid calls forces the limit if no card is on file.

    The free-tier rate-limit error explicitly mentions 'payment method', so we use that string to
    distinguish 'no payment method' from any other rate-limit failure (e.g. exceeding paid limits).
    """
    print("\n5. Voyage AI rate limit / billing")
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print("   [skip] VOYAGE_API_KEY not set")
        return True
    try:
        import voyageai
        from voyageai.error import RateLimitError
    except ImportError:
        print("   [FAIL] voyageai package not installed.")
        return False

    client = voyageai.Client(api_key=api_key)
    print("   Making 4 rapid embed calls to probe rate limit (free tier caps at 3 RPM)...")
    try:
        for _ in range(4):
            client.embed(["ping"], model="voyage-3", input_type="query")
    except RateLimitError as e:
        msg = str(e)
        if "payment method" in msg.lower():
            print("   [FAIL] Free-tier rate limit hit. No payment method on file.")
            print("          Add one at https://dashboard.voyageai.com/ (200M-token voyage-3 free")
            print("          allowance still applies). After adding, wait a few minutes and re-run.")
            return False
        print(f"   [WARN] Rate-limited but error wording does not match the free-tier signal:")
        print(f"          {msg}")
        return True
    print("   [OK] 4 rapid calls succeeded — payment method is configured (rate limit is above free tier).")
    return True


def check_anthropic():
    print("\n6. Anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("   [skip] ANTHROPIC_API_KEY not set")
        return True
    try:
        from anthropic import Anthropic
    except ImportError:
        print("   [FAIL] anthropic package not installed. Run: pip install anthropic")
        return False
    try:
        client = Anthropic(api_key=api_key)
        models = client.models.list(limit=1)
    except Exception as e:
        print(f"   [FAIL] API call failed: {e}")
        return False
    sample = models.data[0].id if models.data else "<none>"
    print(f"   [OK] Authenticated. Sample model id: {sample}")
    return True


def main():
    print("=== hybrid-search environment check ===\n")

    env_ok = check_env_vars()
    if not env_ok:
        print("\nFAILED: required environment variables are missing.")
        sys.exit(1)

    client = check_mongo_ping(os.environ["MONGODB_URI"])
    if client is None:
        sys.exit(2)

    if not check_database(client):
        sys.exit(3)

    if not check_voyage():
        sys.exit(4)

    if not check_voyage_billing():
        sys.exit(5)

    if not check_anthropic():
        sys.exit(6)

    print("\n=== All checks passed. Environment is ready. ===")


if __name__ == "__main__":
    main()
