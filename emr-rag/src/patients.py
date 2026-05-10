"""Patient service. Reads decrypt PHI fields; writes encrypt them."""
from datetime import datetime, timezone

from src.crypto import decrypt_phi, encrypt_phi
from src.db import get_db


def upsert_patient(patient_id: str, fields: dict) -> dict:
    """Create or update a patient. Encrypts PHI on write."""
    payload = {**fields, "registered_at": fields.get("registered_at") or datetime.now(timezone.utc)}
    encrypted = encrypt_phi(payload)
    get_db().patients.update_one(
        {"_id": patient_id},
        {"$set": encrypted},
        upsert=True,
    )
    return decrypt_phi({"_id": patient_id, **payload})


def get_patient(patient_id: str) -> dict | None:
    doc = get_db().patients.find_one({"_id": patient_id})
    return decrypt_phi(doc) if doc else None


def list_patients(limit: int = 50) -> list:
    docs = list(get_db().patients.find().limit(limit))
    return [decrypt_phi(d) for d in docs]


def get_raw_patient(patient_id: str) -> dict | None:
    """Return a patient document *without* decrypting — for debugging / inspector views."""
    return get_db().patients.find_one({"_id": patient_id})
