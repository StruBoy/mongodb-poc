"""Per-archetype anomaly scoring for the fraud-detect PoC.

For each incoming transaction, we run one filtered $vectorSearch per known
fraud archetype against `fraud_examples`. The archetype with the highest top-1
cosine similarity becomes the candidate match; if that score clears the
threshold, the transaction is flagged.

Why per-archetype: a global top-K search collapses to whichever archetype
happens to dominate the corpus by raw token overlap (typically AU/near-home),
even when a different archetype is a much stronger fit. Filtering per archetype
guarantees each one is fairly considered. Pre-filter requires `archetype` to be
in the vector index (see scripts/create_db_index.py).
"""
from src.db import get_db
from src.embed import embed_texts, transaction_to_text

ARCHETYPES = [
    "card_testing", "foreign_cnp", "account_takeover",
]
ANOMALY_THRESHOLD = 0.865  # calibrated empirically — see scripts/calibrate.py
NUM_CANDIDATES = 500       # > per-archetype corpus size so pre-filter sees all of it


def score_transaction(tx: dict) -> dict:
    """Score a transaction by best-fit archetype.

    Returns the original tx enriched with:
      - risk_score:        cosine of the best match across all archetypes
      - top_archetype:     archetype that produced that best match
      - score_gap:         risk_score minus the runner-up archetype's score
      - is_anomaly:        risk_score >= ANOMALY_THRESHOLD
      - matched_archetypes: best match within EACH archetype, sorted by score
    """
    db = get_db()
    text = transaction_to_text(tx)
    [embedding] = embed_texts([text], input_type="query")

    matches_by_archetype = {}
    for arc in ARCHETYPES:
        pipeline = [
            {"$vectorSearch": {
                "index": "fraud_vector_idx",
                "path": "embedding",
                "queryVector": embedding,
                "filter": {"archetype": arc},
                "numCandidates": NUM_CANDIDATES,
                "limit": 1,
            }},
            {"$project": {
                "_id": 0,
                "archetype": 1,
                "archetype_description": 1,
                "merchant_category": 1,
                "amount": 1,
                "country": 1,
                "hour_of_day": 1,
                "score": {"$meta": "vectorSearchScore"},
            }},
        ]
        result = list(db.fraud_examples.aggregate(pipeline))
        if result:
            matches_by_archetype[arc] = result[0]

    if not matches_by_archetype:
        return {**tx, "risk_score": 0.0, "top_archetype": None, "score_gap": 0.0,
                "is_anomaly": False, "matched_archetypes": []}

    sorted_matches = sorted(matches_by_archetype.values(), key=lambda m: -m["score"])
    top, runner_up = sorted_matches[0], (sorted_matches[1] if len(sorted_matches) > 1 else None)
    risk_score = top["score"]
    score_gap = risk_score - runner_up["score"] if runner_up else risk_score

    return {
        **tx,
        "risk_score": round(risk_score, 4),
        "top_archetype": top["archetype"],
        "score_gap": round(score_gap, 4),
        "is_anomaly": risk_score >= ANOMALY_THRESHOLD,
        "matched_archetypes": sorted_matches,
    }
