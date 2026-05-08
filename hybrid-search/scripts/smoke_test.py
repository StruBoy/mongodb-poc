"""Smoke test for the three hybrid-search modes.

Runs the three sidebar demo queries and asserts that each mode wins or fails
where the demo expects it to:

  1. Marathon racing — keyword should miss (banned intent vocabulary in
     descriptions); semantic should surface marathon racing / trail running.
  2. TrailMaster brand — keyword should return only TrailMaster products;
     semantic should drift to other trail-themed brands (brand was stripped
     from the embedded text).
  3. Long flights — both modes should surface headphones / earbuds; hybrid
     should blend them.

Run from the project root:
    python -m scripts.smoke_test

Exits 0 on full success, 1 if the catalog is empty, 2 if any assertion fails.
"""
import sys

from src.core import hybrid_search, keyword_search, semantic_search
from src.db import get_db

LIMIT = 5

# Subtype groups used to judge "semantically relevant" hits per demo query.
RUNNING_SUBTYPES = {"marathon racing shoe", "trail running shoe"}
HEADPHONE_SUBTYPES = {"noise-cancelling headphones", "wireless earbuds"}


def _format_score(score):
    if score is None:
        return "n/a"
    if isinstance(score, (int, float)):
        return f"{score:.4f}"
    if isinstance(score, dict):
        v = score.get("value")
        if isinstance(v, (int, float)):
            return f"{v:.4f}"
        return "details"
    return str(score)


def render(label: str, results: list) -> None:
    print(f"\n  --- {label} ({len(results)} results) ---")
    if not results:
        print("    (no results)")
        return
    for r in results:
        subtype = r.get("_product_type", "?")
        print(
            f"    {r.get('title', '?'):<28} | {r.get('brand', '?'):<14} | "
            f"{subtype:<28} | ${r.get('price', 0):>7.2f} | "
            f"score={_format_score(r.get('score'))}"
        )


def _count_subtypes(results: list, subtypes: set) -> int:
    return sum(1 for r in results if r.get("_product_type") in subtypes)


def _count_brand(results: list, brand: str) -> int:
    return sum(1 for r in results if r.get("brand") == brand)


def assert_marathon(failures: list):
    print("\n[1/3] Marathon racing — keyword should fail, semantic should win")
    query, cat, max_price = "racing 26.2 miles", "footwear", 400
    print(f"      query={query!r}  category={cat}  max_price={max_price}")

    kw = keyword_search(query, cat, max_price, LIMIT)
    sem = semantic_search(query, cat, max_price, LIMIT)
    hyb = hybrid_search(query, cat, max_price, LIMIT)
    render("Keyword", kw)
    render("Semantic", sem)
    render("Hybrid", hyb)

    kw_running = _count_subtypes(kw, RUNNING_SUBTYPES)
    sem_running = _count_subtypes(sem, RUNNING_SUBTYPES)

    print(f"\n      keyword running-shoe hits:  {kw_running}/{LIMIT} (expect ≤ 1)")
    print(f"      semantic running-shoe hits: {sem_running}/{LIMIT} (expect ≥ 3)")

    if kw_running > 1:
        failures.append(
            f"marathon: keyword surfaced {kw_running} running shoes (expected ≤ 1) — "
            "banned intent words may be leaking into descriptions"
        )
    if sem_running < 3:
        failures.append(
            f"marathon: semantic only surfaced {sem_running} running shoes (expected ≥ 3) — "
            "embedding-text change may have stripped too much signal"
        )
    if not hyb:
        failures.append("marathon: hybrid returned 0 results")


def assert_brand(failures: list):
    print("\n[2/3] Brand search — keyword should win, semantic should fail")
    query, cat, max_price = "TrailMaster", "footwear", 500
    print(f"      query={query!r}  category={cat}  max_price={max_price}")

    kw = keyword_search(query, cat, max_price, LIMIT)
    sem = semantic_search(query, cat, max_price, LIMIT)
    hyb = hybrid_search(query, cat, max_price, LIMIT)
    render("Keyword", kw)
    render("Semantic", sem)
    render("Hybrid", hyb)

    kw_brand = _count_brand(kw, "TrailMaster")
    sem_brand = _count_brand(sem, "TrailMaster")

    print(f"\n      keyword TrailMaster hits:  {kw_brand}/{LIMIT} (expect = {LIMIT})")
    print(f"      semantic TrailMaster hits: {sem_brand}/{LIMIT} (expect ≤ 2)")

    if kw_brand < LIMIT:
        failures.append(
            f"brand: keyword surfaced only {kw_brand}/{LIMIT} TrailMaster products — "
            "Atlas Search index or brand field may be misconfigured"
        )
    if sem_brand > 2:
        failures.append(
            f"brand: semantic surfaced {sem_brand}/{LIMIT} TrailMaster products (expected ≤ 2) — "
            "brand may still be leaking into the embedded text via product_to_text"
        )
    if not hyb:
        failures.append("brand: hybrid returned 0 results")


def assert_long_flights(failures: list):
    print("\n[3/3] Long flights — both modes should contribute")
    query, cat, max_price = "comfortable for long flights", "electronics", 800
    print(f"      query={query!r}  category={cat}  max_price={max_price}")

    kw = keyword_search(query, cat, max_price, LIMIT)
    sem = semantic_search(query, cat, max_price, LIMIT)
    hyb = hybrid_search(query, cat, max_price, LIMIT)
    render("Keyword", kw)
    render("Semantic", sem)
    render("Hybrid", hyb)

    kw_phones = _count_subtypes(kw, HEADPHONE_SUBTYPES)
    sem_phones = _count_subtypes(sem, HEADPHONE_SUBTYPES)

    print(f"\n      keyword headphone/earbud hits:  {kw_phones}/{LIMIT} (expect ≥ 1)")
    print(f"      semantic headphone/earbud hits: {sem_phones}/{LIMIT} (expect ≥ 1)")

    if kw_phones < 1:
        failures.append("long_flights: keyword surfaced no headphones/earbuds")
    if sem_phones < 1:
        failures.append("long_flights: semantic surfaced no headphones/earbuds")
    if not hyb:
        failures.append("long_flights: hybrid returned 0 results")


def main():
    coll = get_db().products
    if coll.count_documents({}) == 0:
        print("[FAIL] Catalog is empty. Run: python -m data.generate")
        sys.exit(1)

    failures = []
    assert_marathon(failures)
    assert_brand(failures)
    assert_long_flights(failures)

    print()
    if failures:
        print("[FAIL] Demo differentiation assertions did not all pass:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(2)
    print("[OK] All three demo queries differentiate as expected.")


if __name__ == "__main__":
    main()
