"""Smoke test for the RAG pipeline.

Runs three representative questions through answer_question() and prints the
answer plus its top sources. Run from the project root (ai-kb/):
    python -m scripts.smoke_test

Exits 0 if every question returns a non-empty answer with at least one source,
1 if the corpus is empty, 2 on any per-question failure.
"""
import sys

from src.rag import answer_question

QUESTIONS = [
    ("How much parental leave do we offer?", "hr-001"),
    ("What's our remote work policy?", "hr-004"),
    ("How do I report a phishing email?", "sec-003"),
]


def main():
    failures = 0

    for question, expected_top_id in QUESTIONS:
        print(f"\n=== Q: {question}")
        result = answer_question(question)

        if not result["sources"]:
            print("   [FAIL] No sources retrieved — corpus is likely empty or index not queryable.")
            failures += 1
            continue

        top = result["sources"][0]
        marker = "OK" if top["id"] == expected_top_id else "WARN"
        print(f"   [{marker}] top source: {top['id']} (expected {expected_top_id})  score={top['score']:.4f}")
        print(f"   retrieval={result['retrieval_ms']:.0f}ms  generation={result['generation_ms']:.0f}ms")
        answer = result["answer"].strip().replace("\n", " ")
        if len(answer) > 240:
            answer = answer[:240] + "..."
        print(f"   A: {answer}")
        print(f"   sources:")
        for s in result["sources"]:
            print(f"     - {s['id']}: {s['title']} (score={s['score']:.4f})")
        if not result["answer"].strip():
            print("   [FAIL] Empty answer.")
            failures += 1

    print()
    if failures:
        print(f"[FAIL] {failures} question(s) failed.")
        sys.exit(2)
    print("[OK] All questions answered with retrieved sources.")


if __name__ == "__main__":
    main()
