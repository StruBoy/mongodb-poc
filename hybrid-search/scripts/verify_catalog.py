"""Verify the products collection after running data/generate.py.

Checks that the catalog is loaded with the right shape and content. Run:
    python -m scripts.verify_catalog

Exits 0 on success, 1 if the collection is empty, 2 on any structural failure.
"""
import sys
from collections import Counter

from src.db import get_db

EXPECTED_COUNT = 2500
COUNT_TOLERANCE = 0.10
EXPECTED_CATEGORIES = {"footwear", "electronics", "home", "sports"}
EXPECTED_BRANDS_PER_CATEGORY = 5
EXPECTED_EMBEDDING_DIM = 1024
REQUIRED_FIELDS = {
    "title", "description", "brand", "category",
    "price", "rating", "review_count", "embedding",
}
SHORT_DESCRIPTION_THRESHOLD_CHARS = 80
FALLBACK_RATIO_THRESHOLD = 0.05


def main():
    coll = get_db().products
    failures = []

    # --- 1. Count ---
    print("1. Document count")
    count = coll.count_documents({})
    if count == 0:
        print("   [FAIL] Collection is empty. Run: python -m data.generate")
        sys.exit(1)
    delta = abs(count - EXPECTED_COUNT) / EXPECTED_COUNT
    marker = "OK" if delta <= COUNT_TOLERANCE else "WARN"
    print(f"   [{marker}] {count} docs (expected ~{EXPECTED_COUNT}, off by {delta:.0%})")

    # --- 2. Required fields ---
    print("\n2. Required fields")
    missing = Counter()
    sample_size = min(50, count)
    sample = list(coll.find().limit(sample_size))
    for doc in sample:
        for f in REQUIRED_FIELDS:
            if f not in doc or doc[f] is None:
                missing[f] += 1
    if missing:
        print(f"   [FAIL] Missing in sample of {sample_size}:")
        for f, n in missing.most_common():
            print(f"     {f}: {n}/{sample_size}")
        failures.append("required_fields")
    else:
        print(f"   [OK] All {len(REQUIRED_FIELDS)} required fields present in sample of {sample_size}")

    leaked = coll.count_documents({"_product_type": {"$exists": True}})
    if leaked:
        print(f"   [WARN] {leaked} docs still carry the temp _product_type field")

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

    # --- 5. Brand coverage ---
    print("\n5. Brand coverage per category")
    brand_rows = coll.aggregate([
        {"$group": {"_id": {"category": "$category", "brand": "$brand"}}},
        {"$group": {"_id": "$_id.category", "brands": {"$addToSet": "$_id.brand"}}},
    ])
    for row in brand_rows:
        n = len(row["brands"])
        marker = "OK" if n >= EXPECTED_BRANDS_PER_CATEGORY else "WARN"
        print(f"   [{marker}] {row['_id']}: {n} brands ({sorted(row['brands'])})")

    # --- 6. Description quality ---
    print("\n6. Description quality")
    short_count = coll.count_documents({
        "$expr": {"$lt": [{"$strLenCP": "$description"}, SHORT_DESCRIPTION_THRESHOLD_CHARS]}
    })
    ratio = short_count / count
    marker = "OK" if ratio <= FALLBACK_RATIO_THRESHOLD else "WARN"
    print(
        f"   [{marker}] {short_count}/{count} ({ratio:.0%}) descriptions under "
        f"{SHORT_DESCRIPTION_THRESHOLD_CHARS} chars (likely templated fallbacks from failed Claude batches)"
    )

    # --- 7. Sample document ---
    print("\nSample document (embedding truncated):")
    sample_doc = dict(one)
    emb = sample_doc.pop("embedding", [])
    for k, v in sorted(sample_doc.items()):
        if isinstance(v, str) and len(v) > 100:
            v = v[:100] + "..."
        print(f"  {k}: {v}")
    print(f"  embedding: [{emb[0]:.4f}, {emb[1]:.4f}, ..., {emb[-1]:.4f}] ({len(emb)} dims)")

    if failures:
        print(f"\nFAILED checks: {', '.join(failures)}")
        sys.exit(2)
    print("\n=== Catalog verification passed. ===")


if __name__ == "__main__":
    main()
