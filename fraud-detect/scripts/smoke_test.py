"""Smoke test for the fraud-detect scoring pipeline.

Runs three hand-crafted transactions through score_transaction:
  - a clearly suspicious foreign CNP transaction (should flag)
  - a normal local card-present grocery purchase (should not flag)
  - an off-hours large online purchase (account_takeover-ish — should flag)

Run from the project root (fraud-detect/):
    python -m scripts.smoke_test

Exits 0 if the suspicious cases flag and the normal case does not.
Exits 2 if any case classifies the wrong way.
"""
import sys
from datetime import datetime

from src.core import ANOMALY_THRESHOLD, score_transaction

SUSPICIOUS_FOREIGN = {
    "tx_id": "smoke-suspicious-001",
    "ts": datetime.now(),
    "amount": 1250.00,
    "currency": "AUD",
    "merchant_name": "QuickCash Online",
    "merchant_category": "online_retail",
    "country": "RU",
    "channel": "online",
    "card_present": False,
    "hour_of_day": 3,
    "distance_from_home_km": 14000,
}

NORMAL_LOCAL = {
    "tx_id": "smoke-normal-001",
    "ts": datetime.now(),
    "amount": 42.30,
    "currency": "AUD",
    "merchant_name": "Coles Supermarkets",
    "merchant_category": "groceries",
    "country": "AU",
    "channel": "in_person",
    "card_present": True,
    "hour_of_day": 18,
    "distance_from_home_km": 3.2,
}

SUSPICIOUS_TAKEOVER = {
    "tx_id": "smoke-suspicious-002",
    "ts": datetime.now(),
    "amount": 2400.00,
    "currency": "AUD",
    "merchant_name": "LuxuryGoodsDirect",
    "merchant_category": "electronics",
    "country": "AU",
    "channel": "online",
    "card_present": False,
    "hour_of_day": 3,
    "distance_from_home_km": 8.4,
}

CASES = [
    ("Foreign CNP (should flag)",        SUSPICIOUS_FOREIGN,  True),
    ("Local groceries (should not flag)", NORMAL_LOCAL,       False),
    ("Off-hours large online (should flag)", SUSPICIOUS_TAKEOVER, True),
]


def render(label, tx, expect_anomaly):
    result = score_transaction(tx)
    actual = result["is_anomaly"]
    correct = actual == expect_anomaly
    marker = "OK" if correct else "FAIL"
    arrow = "🚨" if actual else "  "
    print(f"\n[{marker}] {label}")
    print(f"  {arrow} risk_score={result['risk_score']:.4f}  is_anomaly={actual}")
    print(f"     top_archetype={result['top_archetype']}  "
          f"gap={result['score_gap']:+.4f}")
    print(f"  best match per archetype:")
    for m in result["matched_archetypes"]:
        print(f"    - {m['archetype']:<24} score={m['score']:.4f}  "
              f"({m['country']}, {m['merchant_category']}, ${m['amount']:.2f}, "
              f"h={m['hour_of_day']:02d})")
    return correct


def main():
    print(f"Smoke test: threshold={ANOMALY_THRESHOLD}")

    results = [render(label, tx, expect) for label, tx, expect in CASES]

    print()
    if all(results):
        print(f"[OK] All {len(results)} cases classified correctly.")
        sys.exit(0)
    bad = sum(1 for r in results if not r)
    print(f"[FAIL] {bad}/{len(results)} cases misclassified — tune ANOMALY_THRESHOLD or check the corpus.")
    sys.exit(2)


if __name__ == "__main__":
    main()
