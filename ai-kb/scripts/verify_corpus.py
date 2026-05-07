"""Verify the documents collection after running data/generate.py.

Checks shape and content. Run from the project root (ai-kb/):
    python -m scripts.verify_corpus

Exits 0 on success, 1 if the collection is empty, 2 on any structural failure.
"""
import sys
from collections import Counter

from src.db import get_db

EXPECTED_COUNT = 20
EXPECTED_CATEGORIES = {"HR", "IT", "Finance", "Product", "Security"}
EXPECTED_EMBEDDING_DIM = 1024
REQUIRED_FIELDS = {"title", "body", "category", "last_updated", "embedding"}
SHORT_BODY_THRESHOLD_CHARS = 120
FALLBACK_RATIO_THRESHOLD = 0.20


def main():
    coll = get_db().documents
    failures = []

    # --- 1. Count ---
    print("1. Document count")
    count = coll.count_documents({})
    if count == 0:
        print("   [FAIL] Collection is empty. Run: python -m data.generate")
        sys.exit(1)
    marker = "OK" if count == EXPECTED_COUNT else "WARN"
    print(f"   [{marker}] {count} docs (expected {EXPECTED_COUNT})")

    # --- 2. Required fields ---
    print("\n2. Required fields")
    missing = Counter()
    sample = list(coll.find())
    for doc in sample:
        for f in REQUIRED_FIELDS:
            if f not in doc or doc[f] is None:
                missing[f] += 1
    if missing:
        print(f"   [FAIL] Missing in sample of {len(sample)}:")
        for f, n in missing.most_common():
            print(f"     {f}: {n}/{len(sample)}")
        failures.append("required_fields")
    else:
        print(f"   [OK] All {len(REQUIRED_FIELDS)} required fields present in all {len(sample)} docs")

    # --- 3. Embedding shape ---
    print("\n3. Embedding shape")
    one = coll.find_one({"embedding": {"$exists": True}})
    if not one:
        print("   [FAIL] No documents with an embedding field")
        failures.append("embedding_present")
    else:
        dim = len(one["embedding"])
        if dim != EXPECTED_EMBEDDING_DIM:
            print(f"   [FAIL] Got {dim}-dim, expected {EXPECTED_EMBEDDING_DIM}")
            failures.append("embedding_dim")
        else:
            non_numeric = next((v for v in one["embedding"] if not isinstance(v, (int, float))), None)
            if non_numeric is not None:
                print(f"   [FAIL] Embedding contains non-numeric value: {non_numeric!r}")
                failures.append("embedding_dtype")
            else:
                print(f"   [OK] {dim}-dim numeric embedding")

    # --- 4. Category distribution ---
    print("\n4. Category distribution")
    cat_rows = list(coll.aggregate([
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    seen = {row["_id"] for row in cat_rows}
    for row in cat_rows:
        print(f"   {row['_id']}: {row['count']}")
    missing_cats = EXPECTED_CATEGORIES - seen
    extra_cats = seen - EXPECTED_CATEGORIES
    if missing_cats:
        print(f"   [FAIL] Missing categories: {sorted(missing_cats)}")
        failures.append("categories")
    elif extra_cats:
        print(f"   [WARN] Unexpected categories: {sorted(extra_cats)}")
    else:
        print(f"   [OK] All {len(EXPECTED_CATEGORIES)} categories present")

    # --- 5. ID format ---
    print("\n5. Document _id format")
    bad_ids = [d["_id"] for d in sample if not isinstance(d["_id"], str)]
    if bad_ids:
        print(f"   [FAIL] Non-string _ids found: {bad_ids[:3]}")
        failures.append("id_format")
    else:
        print(f"   [OK] All _ids are strings (e.g. {sample[0]['_id']!r})")

    # --- 6. Body quality ---
    print("\n6. Body quality")
    short_count = coll.count_documents({
        "$expr": {"$lt": [{"$strLenCP": "$body"}, SHORT_BODY_THRESHOLD_CHARS]}
    })
    ratio = short_count / count
    marker = "OK" if ratio <= FALLBACK_RATIO_THRESHOLD else "WARN"
    print(
        f"   [{marker}] {short_count}/{count} ({ratio:.0%}) bodies under "
        f"{SHORT_BODY_THRESHOLD_CHARS} chars (likely templated fallbacks from failed Claude calls)"
    )

    # --- 7. Sample document ---
    print("\nSample document (embedding truncated):")
    sample_doc = dict(one)
    emb = sample_doc.pop("embedding", [])
    for k, v in sorted(sample_doc.items()):
        if isinstance(v, str) and len(v) > 120:
            v = v[:120] + "..."
        print(f"  {k}: {v}")
    print(f"  embedding: [{emb[0]:.4f}, {emb[1]:.4f}, ..., {emb[-1]:.4f}] ({len(emb)} dims)")

    if failures:
        print(f"\nFAILED checks: {', '.join(failures)}")
        sys.exit(2)
    print("\n=== Corpus verification passed. ===")


if __name__ == "__main__":
    main()
