"""Retrieval-augmented summary + Q&A scoped to a single patient.

Two entry points:
  * summarize_patient(patient_id, persona) — pulls all of a patient's visits,
    asks Claude haiku 4.5 for a 3-5 sentence overview plus 3 focus-area
    bullets. Persona switches the voice between clinician and patient.
  * answer_question(patient_id, query, persona, k) — patient-scoped
    $vectorSearch, then Claude with persona-appropriate prompt.

Both return retrieval/generation timings so the UI can surface them.
"""
import json
import os
import time
from datetime import datetime, timezone

from anthropic import Anthropic
from dotenv import load_dotenv

from src.db import get_db
from src.embed import embed_texts

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

VECTOR_INDEX_NAME = "kb_visits_idx"
ANSWER_MODEL = "claude-haiku-4-5-20251001"

PERSONAS = {
    "clinical": {
        "label": "GP",
        "summary_system": (
            "You are summarising a patient record for the patient's GP. Use clinical "
            "terminology naturally (lab values, scores, drug doses, ICD-style diagnoses). "
            "Be concise and decisive. If recent visits change the picture from earlier ones, "
            "say so explicitly. Cite visit IDs inline like [vis-003]."
        ),
        "summary_focus_label": "Clinical priorities",
        "answer_system": (
            "You are answering a clinical question about a single patient for their GP. "
            "Use clinical terminology. Cite visit IDs inline like [vis-003]. If the answer "
            "isn't in the visits provided, say so."
        ),
    },
    "patient": {
        "label": "Patient",
        "summary_system": (
            "You are explaining a patient's medical record to the patient themselves. "
            "Use plain English, avoid jargon (or translate it on first use), and keep the "
            "tone calm and reassuring but accurate. Do not invent reassurance the record "
            "doesn't support. If recent visits change things meaningfully, lead with that."
        ),
        "summary_focus_label": "What to focus on",
        "answer_system": (
            "You are answering a question from a patient about their own medical record. "
            "Use plain English, avoid jargon. If the answer isn't in the visits provided, "
            "say so and recommend they ask their GP."
        ),
    },
}


def _format_visit_for_prompt(v: dict) -> str:
    """Render a visit for inclusion in the prompt context block."""
    visit_date = v.get("visit_date")
    if hasattr(visit_date, "date"):
        date_str = visit_date.date().isoformat()
    else:
        date_str = str(visit_date)
    structured = {
        k: v[k]
        for k in v
        if k not in {"_id", "patient_id", "visit_date", "specialty", "provider",
                     "chief_complaint", "body", "embedding", "last_updated", "score"}
        and v[k] is not None
    }
    structured_block = ""
    if structured:
        structured_block = f"\nStructured fields: {json.dumps(structured, default=str)}"
    return (
        f"[Visit {v['_id']} | {date_str} | {v.get('specialty')} | {v.get('provider', '')}]\n"
        f"Chief complaint: {v.get('chief_complaint', '')}\n"
        f"{v.get('body', '')}"
        f"{structured_block}"
    )


def summarize_patient(patient_id: str, persona: str = "clinical") -> dict:
    """Generate a 3-5 sentence overview plus 3 focus-area bullets for one patient."""
    if persona not in PERSONAS:
        raise ValueError(f"persona must be one of {list(PERSONAS)}")
    cfg = PERSONAS[persona]
    db = get_db()

    t0 = time.perf_counter()
    visits = list(db.visits.find({"patient_id": patient_id}, {"embedding": 0}).sort("visit_date", 1))
    retrieval_ms = (time.perf_counter() - t0) * 1000

    if not visits:
        return {
            "summary": "No visits on file for this patient yet.",
            "focus_areas": [],
            "focus_label": cfg["summary_focus_label"],
            "sources": [],
            "visit_count": 0,
            "retrieval_ms": retrieval_ms,
            "generation_ms": 0.0,
            "generated_at": datetime.now(timezone.utc),
        }

    context_block = "\n\n---\n\n".join(_format_visit_for_prompt(v) for v in visits)

    prompt = f"""{cfg['summary_system']}

You will be given the patient's complete visit history (all specialties, chronological).

Produce a JSON object with exactly two fields:
- "summary": a 3-5 sentence overview of the patient's overall situation, including the current trajectory and prognosis. If recent visits have materially shifted the picture (new diagnosis, treatment change, prognosis update) make that explicit.
- "focus_areas": an array of exactly 3 short bullet strings (one sentence each) — the most important things to focus on right now.

Return ONLY the JSON object (no preamble, no fences, no explanation).

Patient visits (chronological):
{context_block}
"""

    t1 = time.perf_counter()
    response = anthropic.messages.create(
        model=ANSWER_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    generation_ms = (time.perf_counter() - t1) * 1000

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
        summary = parsed.get("summary", "").strip()
        focus_areas = [s.strip() for s in parsed.get("focus_areas", []) if s and s.strip()]
    except (json.JSONDecodeError, AttributeError):
        # Fallback: surface the raw text rather than failing the demo.
        summary = text
        focus_areas = []

    return {
        "summary": summary,
        "focus_areas": focus_areas,
        "focus_label": cfg["summary_focus_label"],
        "sources": [
            {"id": v["_id"], "specialty": v.get("specialty"),
             "visit_date": v.get("visit_date"), "provider": v.get("provider")}
            for v in visits
        ],
        "visit_count": len(visits),
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "generated_at": datetime.now(timezone.utc),
    }


def retrieve_visits(patient_id: str, query: str, k: int = 4,
                    specialty: str | None = None) -> list[dict]:
    """Patient-scoped $vectorSearch over the visits collection."""
    db = get_db()
    [embedding] = embed_texts([query], input_type="query")

    filter_clause: dict = {"patient_id": patient_id}
    if specialty:
        filter_clause["specialty"] = specialty

    pipeline = [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX_NAME,
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": 50,
                "limit": k,
                "filter": filter_clause,
            }
        },
        {
            "$project": {
                "_id": 1,
                "patient_id": 1,
                "visit_date": 1,
                "specialty": 1,
                "provider": 1,
                "chief_complaint": 1,
                "body": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
    return list(db.visits.aggregate(pipeline))


def answer_question(patient_id: str, query: str, persona: str = "clinical",
                    k: int = 4, specialty: str | None = None) -> dict:
    if persona not in PERSONAS:
        raise ValueError(f"persona must be one of {list(PERSONAS)}")
    cfg = PERSONAS[persona]

    t0 = time.perf_counter()
    visits = retrieve_visits(patient_id, query, k=k, specialty=specialty)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    if not visits:
        return {
            "answer": "I couldn't find any visits matching that question.",
            "sources": [],
            "retrieval_ms": retrieval_ms,
            "generation_ms": 0.0,
        }

    context_block = "\n\n---\n\n".join(_format_visit_for_prompt(v) for v in visits)

    prompt = f"""{cfg['answer_system']}

Patient visits relevant to the question:
{context_block}

Question: {query}

Answer:"""

    t1 = time.perf_counter()
    response = anthropic.messages.create(
        model=ANSWER_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    generation_ms = (time.perf_counter() - t1) * 1000

    return {
        "answer": response.content[0].text,
        "sources": [
            {
                "id": v["_id"],
                "specialty": v.get("specialty"),
                "visit_date": v.get("visit_date"),
                "provider": v.get("provider"),
                "score": v.get("score"),
            }
            for v in visits
        ],
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
    }
