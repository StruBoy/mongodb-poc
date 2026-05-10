"""Visit service with auto-embedding on every write.

Every upsert regenerates the embedding via voyage-3 on the same write path,
so a new visit's content is searchable and summarisable the instant the
insert returns — no separate re-embed pipeline.
"""
from datetime import datetime, timezone

from src.db import get_db
from src.embed import embed_texts, visit_to_text


def upsert_visit(visit: dict) -> dict:
    """Create or update a visit. Re-embeds via voyage-3 on every write.

    `visit` must contain at minimum: _id, patient_id, visit_date, specialty,
    provider, chief_complaint, body. Specialty-specific structured fields
    (phq9_score, hba1c_pct, ahi_per_hour, etc.) pass through verbatim.
    """
    payload = {**visit, "last_updated": datetime.now(timezone.utc)}
    [embedding] = embed_texts([visit_to_text(payload)], input_type="document")
    payload["embedding"] = embedding
    get_db().visits.update_one(
        {"_id": payload["_id"]},
        {"$set": {k: v for k, v in payload.items() if k != "_id"}},
        upsert=True,
    )
    return {k: v for k, v in payload.items() if k != "embedding"}


def get_visits_for_patient(patient_id: str) -> list[dict]:
    """All visits for a patient, oldest first, no embedding."""
    return list(
        get_db().visits.find({"patient_id": patient_id}, {"embedding": 0}).sort("visit_date", 1)
    )


def get_visit(visit_id: str) -> dict | None:
    return get_db().visits.find_one({"_id": visit_id}, {"embedding": 0})


def add_followup_batch(patient_id: str) -> list[str]:
    """Insert the canned follow-up batch for the demo. Returns the list of inserted visit IDs.

    Each visit goes through upsert_visit, so each is auto-embedded. This is
    the path the Streamlit "➕ Add follow-up visits" button calls.
    """
    from data.followup_visits import FOLLOWUP_VISITS

    inserted = []
    for v in FOLLOWUP_VISITS:
        visit = {**v, "patient_id": patient_id}
        upsert_visit(visit)
        inserted.append(visit["_id"])
    return inserted


def remove_followup_batch() -> int:
    """Delete the canned follow-up visits. Used by smoke_test cleanup and a demo reset button."""
    from data.followup_visits import FOLLOWUP_VISITS

    ids = [v["_id"] for v in FOLLOWUP_VISITS]
    result = get_db().visits.delete_many({"_id": {"$in": ids}})
    return result.deleted_count
