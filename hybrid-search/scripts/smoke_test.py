"""Smoke test for the three hybrid-search modes.

Runs keyword, semantic, and hybrid searches against a representative query.
Run from the project root:
    python -m scripts.smoke_test

Exits 0 only when all three modes return at least one result. Exits 1 if all
three return zero (catalog likely not loaded). Exits 2 if any single mode
fails or returns zero while others succeed.
"""
import sys

from src.core import hybrid_search, keyword_search, semantic_search

QUERY = "shoes for running long distances"
CATEGORY = "footwear"
MAX_PRICE = 400
LIMIT = 5


def render(label: str, results: list) -> bool:
    print(f"\n=== {label} ({len(results)} results) ===")
    if not results:
        print("   (no results)")
        return False
    for r in results:
        score = r.get("score")
        score_str = _format_score(score)
        print(
            f"  {r.get('title', '?'):<32} | {r.get('brand', '?'):<14} | "
            f"{r.get('category', '?'):<12} | ${r.get('price', 0):>7.2f} | "
            f"score={score_str}"
        )
    return True


def _format_score(score):
    if score is None:
        return "n/a"
    if isinstance(score, (int, float)):
        return f"{score:.4f}"
    if isinstance(score, dict):
        # $rankFusion returns scoreDetails as a doc — surface the top-level value.
        v = score.get("value")
        if isinstance(v, (int, float)):
            return f"{v:.4f}"
        return "details"
    return str(score)


def main():
    print(f"Smoke test: {QUERY!r}")
    print(f"Filters: category={CATEGORY}, max_price={MAX_PRICE}, limit={LIMIT}")

    kw_ok = render("Keyword (Atlas Search)", keyword_search(QUERY, CATEGORY, MAX_PRICE, LIMIT))
    sem_ok = render("Semantic (Vector Search)", semantic_search(QUERY, CATEGORY, MAX_PRICE, LIMIT))
    hyb_ok = render("Hybrid ($rankFusion)", hybrid_search(QUERY, CATEGORY, MAX_PRICE, LIMIT))

    print()
    if not (kw_ok or sem_ok or hyb_ok):
        print("[FAIL] All three searches returned 0 results.")
        print("       Likely cause: catalog not loaded or indexes still building.")
        print("       Run: python -m data.generate")
        print("       Then: python -m scripts.create_db_index")
        sys.exit(1)
    if not (kw_ok and sem_ok and hyb_ok):
        missing = [name for name, ok in [("keyword", kw_ok), ("semantic", sem_ok), ("hybrid", hyb_ok)] if not ok]
        print(f"[WARN] Some modes returned 0 results: {', '.join(missing)}")
        sys.exit(2)
    print("[OK] All three modes returned results.")


if __name__ == "__main__":
    main()
