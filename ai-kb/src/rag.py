"""Retrieval-augmented question answering over kb_demo.documents.

retrieve_context() runs $vectorSearch against the voyage-3 embeddings.
answer_question() composes the retrieved docs into a Claude prompt and
returns both the answer and the sources used.
"""
import os
import time

from anthropic import Anthropic
from dotenv import load_dotenv

from src.db import get_db
from src.embed import embed_texts

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

VECTOR_INDEX_NAME = "kb_vector_idx"
ANSWER_MODEL = "claude-haiku-4-5-20251001"


def retrieve_context(query: str, k: int = 4, category: str | None = None) -> list[dict]:
    db = get_db()
    [embedding] = embed_texts([query], input_type="query")

    vector_stage: dict = {
        "$vectorSearch": {
            "index": VECTOR_INDEX_NAME,
            "path": "embedding",
            "queryVector": embedding,
            "numCandidates": 50,
            "limit": k,
        }
    }
    if category:
        vector_stage["$vectorSearch"]["filter"] = {"category": category}

    pipeline = [
        vector_stage,
        {"$project": {
            "_id": 1,
            "title": 1,
            "body": 1,
            "category": 1,
            "last_updated": 1,
            "score": {"$meta": "vectorSearchScore"},
        }},
    ]
    return list(db.documents.aggregate(pipeline))


def answer_question(query: str, k: int = 4, category: str | None = None) -> dict:
    t0 = time.perf_counter()
    docs = retrieve_context(query, k=k, category=category)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    if not docs:
        return {
            "answer": "I couldn't find any relevant documents in the knowledge base to answer that.",
            "sources": [],
            "retrieval_ms": retrieval_ms,
            "generation_ms": 0.0,
        }

    context_block = "\n\n---\n\n".join(
        f"[Document {d['_id']}: {d['title']}]\n{d['body']}" for d in docs
    )

    prompt = f"""You are an internal company assistant. Answer the user's question using ONLY the documents provided below.

If the answer isn't in the documents, say so. Cite the document IDs you used inline (e.g. "[hr-001]").

Documents:
{context_block}

Question: {query}

Answer:"""

    t1 = time.perf_counter()
    response = anthropic.messages.create(
        model=ANSWER_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    generation_ms = (time.perf_counter() - t1) * 1000

    return {
        "answer": response.content[0].text,
        "sources": [
            {"id": d["_id"], "title": d["title"], "category": d["category"], "score": d["score"]}
            for d in docs
        ],
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
    }
