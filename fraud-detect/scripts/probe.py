"""Diagnostic probe: for a fixed query transaction, report per-archetype score distribution.

For each known archetype, runs a $vectorSearch filtered to that archetype and
reports the top-1 / top-5 / mean cosine. Surfaces whether the corpus is
actually returning archetype-relevant matches or whether voyage-3 is collapsing
everything to a narrow band.

Run from the project root (fraud-detect/):
    python -m scripts.probe
"""
from datetime import datetime

from src.db import get_db
from src.embed import embed_texts, transaction_to_text

ARCHETYPES = ["card_testing", "foreign_cnp", "account_takeover", "amount_anomaly", "merchant_category_fraud"]

QUERIES = {
    "Foreign CNP RU $1250 03:00": {
        "amount": 1250.00, "currency": "AUD",
        "merchant_name": "QuickCash", "merchant_category": "online_retail",
        "country": "RU", "channel": "online", "card_present": False,
        "hour_of_day": 3, "distance_from_home_km": 14000,
    },
    "Local groceries $42 18:00": {
        "amount": 42.30, "currency": "AUD",
        "merchant_name": "Coles", "merchant_category": "groceries",
        "country": "AU", "channel": "in_person", "card_present": True,
        "hour_of_day": 18, "distance_from_home_km": 3.2,
    },
    "Off-hours large $2400 03:00": {
        "amount": 2400.00, "currency": "AUD",
        "merchant_name": "Lux", "merchant_category": "electronics",
        "country": "AU", "channel": "online", "card_present": False,
        "hour_of_day": 3, "distance_from_home_km": 8.4,
    },
}


def main():
    db = get_db()
    print("Corpus archetype counts:")
    for arc in ARCHETYPES:
        print(f"  {arc}: {db.fraud_examples.count_documents({'archetype': arc})}")
    print()
    print("Per-archetype score probe (top-5 cosine within each archetype, numCandidates=2000)\n")
    for label, tx in QUERIES.items():
        text = transaction_to_text(tx)
        print(f"=== {label} ===")
        print(f"text: {text}\n")
        [emb] = embed_texts([text], input_type="query")

        rows = []
        for arc in ARCHETYPES:
            pipeline = [
                {"$vectorSearch": {
                    "index": "fraud_vector_idx",
                    "path": "embedding",
                    "queryVector": emb,
                    "filter": {"archetype": arc},
                    "numCandidates": 2000,
                    "limit": 5,
                }},
                {"$project": {"_id": 0, "archetype": 1, "amount": 1, "country": 1,
                              "merchant_category": 1, "hour_of_day": 1,
                              "score": {"$meta": "vectorSearchScore"}}},
            ]
            matches = list(db.fraud_examples.aggregate(pipeline))
            if not matches:
                rows.append((arc, None, None, None, None))
                continue
            top1 = matches[0]["score"]
            mean5 = sum(m["score"] for m in matches) / len(matches)
            sample = matches[0]
            rows.append((arc, top1, mean5, sample, matches))

        rows.sort(key=lambda r: -(r[1] or 0))
        print(f"  {'archetype':<26} {'top1':>8} {'mean5':>8}  best-match")
        for arc, top1, mean5, sample, _matches in rows:
            if top1 is None:
                print(f"  {arc:<26} (no matches)")
                continue
            s = sample
            print(
                f"  {arc:<26} {top1:>8.4f} {mean5:>8.4f}  "
                f"{s['country']} ${s['amount']:.2f} {s['merchant_category']} h={s['hour_of_day']:02d}"
            )
        print()


if __name__ == "__main__":
    main()
