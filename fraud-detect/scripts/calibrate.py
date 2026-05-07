"""Calibrate the fraud detector against a real sample of normal transactions.

Pulls N transactions at random from the `transactions` collection (all labeled
'normal'), scores each through the same pipeline the dashboard uses, and reports
the distribution of risk_score, top_archetype, and consensus_count.

Use the output to pick ANOMALY_THRESHOLD and CONSENSUS_MIN such that the
false-positive rate on this normal sample is acceptable.

Run from the project root (fraud-detect/):
    python -m scripts.calibrate
"""
import random
from collections import Counter

from src.core import ANOMALY_THRESHOLD, score_transaction
from src.db import get_db

SAMPLE_SIZE = 100
FRAUD_PER_ARCHETYPE = 20


def percentile(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def main():
    db = get_db()
    sample = list(db.transactions.aggregate([{"$sample": {"size": SAMPLE_SIZE}}]))
    print(f"Calibrating on {len(sample)} random normal transactions "
          f"(threshold={ANOMALY_THRESHOLD})\n")

    results = []
    for i, tx in enumerate(sample, 1):
        tx.pop("_id", None)
        results.append(score_transaction(tx))
        if i % 20 == 0:
            print(f"  scored {i}/{len(sample)}")

    risk_scores = [r["risk_score"] for r in results]
    gaps = [r["score_gap"] for r in results]
    flagged = [r for r in results if r["is_anomaly"]]
    arc_counter = Counter(r["top_archetype"] for r in results)

    print(f"\n=== Risk score distribution (best-archetype top-1 cosine) ===")
    for p in (5, 25, 50, 75, 90, 95, 99):
        print(f"  p{p:>2}: {percentile(risk_scores, p):.4f}")
    print(f"  min: {min(risk_scores):.4f}  max: {max(risk_scores):.4f}")

    print(f"\n=== Score gap (top archetype vs runner-up) ===")
    for p in (5, 25, 50, 75, 90, 95, 99):
        print(f"  p{p:>2}: {percentile(gaps, p):+.4f}")

    print(f"\n=== Top archetype on normal transactions ===")
    for arc, n in arc_counter.most_common():
        print(f"  {arc:<24} {n:>3}/{len(results)} ({100*n/len(results):.0f}%)")

    print(f"\n=== Current rule outcome ===")
    print(f"  Flagged: {len(flagged)}/{len(results)} ({100*len(flagged)/len(results):.1f}%)")
    print(f"  Target on normals: <5% (ideally <1%)")

    print(f"\n=== Sweep: false-positive rate by threshold ===")
    for t in (0.78, 0.80, 0.82, 0.83, 0.84, 0.85, 0.86, 0.87, 0.88):
        fp = sum(1 for r in results if r["risk_score"] >= t)
        print(f"  threshold={t:.2f}: {fp:>3}/{len(results)} flagged")

    # --- True-positive sweep against generated fraud examples ---
    from data.generate import FRAUD_ARCHETYPES, generate_fraud_example
    print(f"\n=== True-positive rate by archetype ({FRAUD_PER_ARCHETYPE} samples each, threshold={ANOMALY_THRESHOLD}) ===")
    for name, arc in FRAUD_ARCHETYPES.items():
        scored = [score_transaction(generate_fraud_example(name, arc)) for _ in range(FRAUD_PER_ARCHETYPE)]
        flagged = sum(1 for r in scored if r["is_anomaly"])
        scores = sorted(r["risk_score"] for r in scored)
        correct_arc = sum(1 for r in scored if r["top_archetype"] == name)
        print(f"  {name:<24} flagged={flagged:>2}/{FRAUD_PER_ARCHETYPE}  "
              f"top-arc-correct={correct_arc:>2}/{FRAUD_PER_ARCHETYPE}  "
              f"score min/median/max={scores[0]:.4f}/{scores[len(scores)//2]:.4f}/{scores[-1]:.4f}")


if __name__ == "__main__":
    main()
