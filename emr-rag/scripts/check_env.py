"""Verify the local environment is configured correctly for the emr-rag PoC.

Checks:
  1. .env loads and required variables are present
  2. The MongoDB URI authenticates and the cluster responds to a ping
  3. The emr_demo database and patients/visits collections are reachable
  4. ENCRYPTION_KEY decodes to 32 bytes and round-trips a string through AES-GCM
  5. Voyage AI accepts the API key and returns an embedding
  6. Voyage AI rate limit is above the free tier (i.e. payment method is configured)
  7. Anthropic accepts the API key and returns a model list

Run from the project root (emr-rag/):
    python -m scripts.check_env
"""
import base64
import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError

load_dotenv()

DB_NAME = "emr_demo"
COLLECTIONS = ["patients", "visits"]

REQUIRED_VARS = ["MONGODB_URI", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY", "ENCRYPTION_KEY"]


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
            print(f"   [OK] Collection '{name}' exists with ~{count} documents.")
        else:
            print(f"   [info] Collection '{name}' not yet created.")
    return True


def check_encryption_roundtrip():
    print("\n4. Encryption key")
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        print("   [FAIL] ENCRYPTION_KEY not set")
        return False
    try:
        decoded = base64.b64decode(key)
    except Exception as e:
        print(f"   [FAIL] ENCRYPTION_KEY is not valid base64: {e}")
        return False
    if len(decoded) != 32:
        print(f"   [FAIL] ENCRYPTION_KEY must decode to 32 bytes (AES-256); got {len(decoded)}")
        return False

    try:
        from src.crypto import decrypt_field, encrypt_field
    except ImportError as e:
        print(f"   [FAIL] Could not import src.crypto: {e}")
        return False

    sample = "S1234567A"
    envelope = encrypt_field(sample)
    decrypted = decrypt_field(envelope)
    if decrypted != sample:
        print(f"   [FAIL] Round-trip mismatch: encrypted '{sample}' -> decrypted '{decrypted}'")
        return False

    print(f"   [OK] AES-256-GCM round-trip succeeded.")
    print(f"        Sample envelope keys: {sorted(envelope.keys())}")
    return True


def check_voyage():
    print("\n5. Voyage AI")
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print("   [FAIL] VOYAGE_API_KEY not set")
        return False
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
    """Probe Voyage's rate limit. Free tier = 3 RPM; 4 rapid calls forces the limit if no card is on file."""
    print("\n6. Voyage AI rate limit / billing")
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print("   [FAIL] VOYAGE_API_KEY not set")
        return False
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
    print("   [OK] 4 rapid calls succeeded — payment method is configured.")
    return True


def check_anthropic():
    print("\n7. Anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("   [FAIL] ANTHROPIC_API_KEY not set")
        return False
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
    print("=== emr-rag environment check ===\n")

    if not check_env_vars():
        print("\nFAILED: required environment variables are missing.")
        sys.exit(1)

    client = check_mongo_ping(os.environ["MONGODB_URI"])
    if client is None:
        sys.exit(2)

    if not check_database(client):
        sys.exit(3)

    if not check_encryption_roundtrip():
        sys.exit(4)

    if not check_voyage():
        sys.exit(5)

    if not check_voyage_billing():
        sys.exit(6)

    if not check_anthropic():
        sys.exit(7)

    print("\n=== All checks passed. Environment is ready. ===")


if __name__ == "__main__":
    main()
