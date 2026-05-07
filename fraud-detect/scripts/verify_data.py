"""Verify the fraud_examples and transactions collections after running data/generate.py.

Confirms both collections loaded with the right shape, counts, and field coverage.
Run from the project root (fraud-detect/):
    python -m scripts.verify_data

Exits 0 on success, 1 if either collection is empty, 2 on any structural failure.
"""
import sys
from collections import Counter

from src.db import get_db

EXPECTED_FRAUD_COUNT = 2000
EXPECTED_NORMAL_COUNT = 100000
COUNT_TOLERANCE = 0.05

EXPECTED_ARCHETYPES = {
    "card_testing", "foreign_cnp", "account_takeover",
    "amount_anomaly", "merchant_category_fraud",
}
EXPECTED_PER_ARCHETYPE = 400

EXPECTED_EMBEDDING_DIM = 1024

COMMON_FIELDS = {
    "tx_id", "ts", "amount", "currency", "merchant_name", "merchant_category",
    "country", "channel", "card_present", "hour_of_day", "distance_from_home_km", "label",
}
FRAUD_FIELDS = COMMON_FIELDS | {"archetype", "archetype_description", "embedding"}
NORMAL_FIELDS = COMMON_FIELDS


def check_count(coll, expected, label):
    print(f"\n{label} count")
    count = coll.count_documents({})
    if count == 0:
        print(f"   [FAIL] Collection is empty.")
        return count, False
    delta = abs(count - expected) / expected
    marker = "OK" if delta <= COUNT_TOLERANCE else "WARN"
    print(f"   [{marker}] {count} docs (expected ~{expected}, off by {delta:.0%})")
    return count, True


def check_required_fields(coll, required, label, sample_size=50):
    print(f"\n{label} required fields")
    sample = list(coll.find().limit(sample_size))
    missing = Counter()
    for doc in sample:
        for f in required:
            if f not in doc or doc[f] is None:
                missing[f] += 1
    if missing:
        print(f"   [FAIL] Missing in sample of {len(sample)}:")
        for f, n in missing.most_common():
            print(f"     {f}: {n}/{len(sample)}")
        return False
    print(f"   [OK] All {len(required)} required fields present in sample of {len(sample)}")
    return True


def main():
    db = get_db()
    failures = []

    # --- fraud_examples ---
    print("=== fraud_examples ===")
    fraud = db.fraud_examples
    fraud_count, fraud_ok = check_count(fraud, EXPECTED_FRAUD_COUNT, "1.")
    if not fraud_ok:
        sys.exit(1)

    if not check_required_fields(fraud, FRAUD_FIELDS, "2."):
        failures.append("fraud_required_fields")

    # Embedding shape
    print("\n3. fraud_examples embedding shape")
    one = fraud.find_one({"embedding": {"$exists": True}})
    if not one:
        print("   [FAIL] No fraud_examples with an embedding field")
        failures.append("fraud_embedding_present")
    else:
        dim = len(one["embedding"])
        if dim != EXPECTED_EMBEDDING_DIM:
            print(f"   [FAIL] Got {dim}-dim, expected {EXPECTED_EMBEDDING_DIM}")
            failures.append("fraud_embedding_dim")
        elif not all(isinstance(v, (int, float)) for v in one["embedding"][:5]):
            print(f"   [FAIL] Embedding contains non-numeric values")
            failures.append("fraud_embedding_dtype")
        else:
            print(f"   [OK] {dim}-dim numeric embedding")

    # Archetype distribution
    print("\n4. Archetype distribution")
    arc_rows = list(fraud.aggregate([
        {"$group": {"_id": "$archetype", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    seen = {row["_id"] for row in arc_rows}
    for row in arc_rows:
        n = row["count"]
        marker = "OK" if abs(n - EXPECTED_PER_ARCHETYPE) <= 10 else "WARN"
        print(f"   [{marker}] {row['_id']}: {n}")
    missing_arc = EXPECTED_ARCHETYPES - seen
    extra_arc = seen - EXPECTED_ARCHETYPES
    if missing_arc:
        print(f"   [FAIL] Missing archetypes: {sorted(missing_arc)}")
        failures.append("archetypes")
    if extra_arc:
        print(f"   [WARN] Unexpected archetypes: {sorted(extra_arc)}")

    # Label sanity
    print("\n5. fraud_examples labels")
    bad_labels = fraud.count_documents({"label": {"$ne": "fraud"}})
    if bad_labels:
        print(f"   [FAIL] {bad_labels} docs with label != 'fraud'")
        failures.append("fraud_labels")
    else:
        print(f"   [OK] All {fraud_count} docs labeled 'fraud'")

    # --- transactions ---
    print("\n=== transactions ===")
    tx = db.transactions
    tx_count, tx_ok = check_count(tx, EXPECTED_NORMAL_COUNT, "6.")
    if not tx_ok:
        sys.exit(1)

    if not check_required_fields(tx, NORMAL_FIELDS, "7."):
        failures.append("tx_required_fields")

    # Confirm transactions don't carry embeddings (waste of space)
    print("\n8. transactions has no stray embeddings")
    with_emb = tx.count_documents({"embedding": {"$exists": True}})
    if with_emb:
        print(f"   [WARN] {with_emb} transactions carry an embedding field (expected 0)")
    else:
        print(f"   [OK] No embeddings on transactions (corpus lives in fraud_examples)")

    # Label sanity
    print("\n9. transactions labels")
    bad_labels = tx.count_documents({"label": {"$ne": "normal"}})
    if bad_labels:
        print(f"   [FAIL] {bad_labels} transactions with label != 'normal'")
        failures.append("tx_labels")
    else:
        print(f"   [OK] All {tx_count} transactions labeled 'normal'")

    # --- Sample documents ---
    print("\nSample fraud_examples document (embedding truncated):")
    sample = dict(one)
    emb = sample.pop("embedding", [])
    for k, v in sorted(sample.items()):
        if isinstance(v, str) and len(v) > 80:
            v = v[:80] + "..."
        print(f"  {k}: {v}")
    print(f"  embedding: [{emb[0]:.4f}, {emb[1]:.4f}, ..., {emb[-1]:.4f}] ({len(emb)} dims)")

    print("\nSample transactions document:")
    sample_tx = tx.find_one()
    for k, v in sorted(sample_tx.items()):
        if isinstance(v, str) and len(v) > 80:
            v = v[:80] + "..."
        print(f"  {k}: {v}")

    if failures:
        print(f"\nFAILED checks: {', '.join(failures)}")
        sys.exit(2)
    print("\n=== Data verification passed. ===")


if __name__ == "__main__":
    main()
