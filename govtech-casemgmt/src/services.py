"""Service layer for the citizen-services PoC.

Reads always run through decrypt_pii so callers see plaintext. Writes always
run through encrypt_pii so the database only stores ciphertext for sensitive
fields. The two functions are idempotent against already-encrypted / already-
plaintext values.
"""
from datetime import datetime, timezone

from pymongo.errors import OperationFailure

from src.crypto import decrypt_pii, encrypt_pii
from src.db import get_db

SEARCH_INDEX_NAME = "cases_search_idx"

# Statuses that mean "not yet resolved" across heterogeneous case types
OPEN_STATUSES = {
    "pending",
    "open",
    "investigating",
    "under_review",
    "pending_assessment",
    "additional_info_required",
}


def get_citizen(citizen_id: str) -> dict:
    doc = get_db().citizens.find_one({"_id": citizen_id})
    return decrypt_pii(doc) if doc else None


def list_citizens(limit: int = 50) -> list:
    docs = list(get_db().citizens.find().limit(limit))
    return [decrypt_pii(d) for d in docs]


def get_cases_for_citizen(citizen_id: str) -> list:
    docs = list(
        get_db().cases.find({"citizen_id": citizen_id}).sort("created_at", -1)
    )
    return [decrypt_pii(d) for d in docs]


def search_cases(query: str, limit: int = 20) -> list:
    """Atlas Search across every case type at once.

    `dynamic: true` on the index means we can search every field that appears
    in any document, regardless of which case-type schema it came from. The
    polymorphism advantage in one query.
    """
    pipeline = [
        {
            "$search": {
                "index": SEARCH_INDEX_NAME,
                "text": {"query": query, "path": {"wildcard": "*"}},
            }
        },
        {"$limit": limit},
        {"$addFields": {"score": {"$meta": "searchScore"}}},
    ]
    try:
        docs = list(get_db().cases.aggregate(pipeline))
    except OperationFailure as e:
        # Index not ready yet, or wrong name. Surface a useful error.
        raise RuntimeError(
            f"Atlas Search query failed (is '{SEARCH_INDEX_NAME}' built and queryable?): {e}"
        ) from e
    return [decrypt_pii(d) for d in docs]


def case_summary_by_type() -> list:
    """Counts and open-counts grouped by case_type — feeds the officer dashboard."""
    pipeline = [
        {
            "$group": {
                "_id": "$case_type",
                "count": {"$sum": 1},
                "open": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", list(OPEN_STATUSES)]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {"$sort": {"_id": 1}},
    ]
    return list(get_db().cases.aggregate(pipeline))


def total_case_count() -> int:
    return get_db().cases.estimated_document_count()


def add_new_case_type(case_type: str, citizen_id: str, custom_fields: dict) -> str:
    """Insert a never-before-seen case type into the same collection.

    Demonstrates schema-on-read agility: no migration, no downtime, no schema
    deployment. The dynamic Atlas Search index will index the new fields on
    its next refresh cycle (seconds, not minutes).
    """
    case = {
        "case_type": case_type,
        "citizen_id": citizen_id,
        "status": custom_fields.pop("status", "pending"),
        "created_at": datetime.now(timezone.utc),
        **custom_fields,
    }
    encrypted = encrypt_pii(case)
    result = get_db().cases.insert_one(encrypted)
    return str(result.inserted_id)


def get_raw_citizen(citizen_id: str = None) -> dict:
    """Return a citizen document *without* decrypting — for the inspector view."""
    db = get_db()
    if citizen_id:
        return db.citizens.find_one({"_id": citizen_id})
    return db.citizens.find_one()


def get_raw_case(case_type: str = None) -> dict:
    """Return a case document *without* decrypting — for the inspector view."""
    db = get_db()
    if case_type:
        return db.cases.find_one({"case_type": case_type})
    return db.cases.find_one()
