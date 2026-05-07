"""Document service with auto-embedding on every write.

This is the architectural piece that makes the live-update demo work: every
upsert regenerates the embedding, so a document edit propagates to retrieval
without any separate sync pipeline.
"""
from datetime import datetime, timezone

from src.db import get_db
from src.embed import doc_to_text, embed_texts


def upsert_document(doc_id: str, title: str, body: str, category: str) -> dict:
    """Create or update a document. Re-embeds on every write."""
    db = get_db()
    payload = {
        "title": title,
        "body": body,
        "category": category,
        "last_updated": datetime.now(timezone.utc),
    }
    [embedding] = embed_texts([doc_to_text(payload)], input_type="document")
    payload["embedding"] = embedding
    db.documents.update_one(
        {"_id": doc_id},
        {"$set": payload},
        upsert=True,
    )
    return {k: v for k, v in payload.items() if k != "embedding"}


def get_document(doc_id: str) -> dict | None:
    db = get_db()
    return db.documents.find_one({"_id": doc_id}, {"embedding": 0})


def list_documents() -> list[dict]:
    db = get_db()
    return list(db.documents.find({}, {"embedding": 0}).sort([("category", 1), ("_id", 1)]))
