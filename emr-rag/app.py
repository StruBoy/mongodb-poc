"""Streamlit UI for the emr-rag PoC.

Single-pane layout per patient with:
  - Sidebar persona toggle (GP / Patient) + demo prompts + add-follow-up button
  - Patient header (decrypts PHI on render)
  - AI overview card (3-5 sentence prognosis + focus-area bullets)
  - Visit timeline (chronological, expandable per visit, color-coded by specialty)
  - Patient-scoped Q&A chat with sources

The persona toggle changes the prompts in src.rag without changing the data
path. Same MongoDB collection, two voices.

Run from the project root (emr-rag/):
    streamlit run app.py
"""
from datetime import datetime, timezone

import streamlit as st

from src.patients import get_patient, list_patients
from src.rag import PERSONAS, answer_question, summarize_patient
from src.visits import add_followup_batch, get_visits_for_patient, remove_followup_batch

st.set_page_config(page_title="EMR RAG", layout="wide")
st.title("🩺 Unified Patient Record")
st.caption(
    "MongoDB Atlas + Voyage AI: visit notes from every specialty live in one collection. "
    "AI summary updates the moment new visits land. PHI encrypted at rest."
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "chat" not in st.session_state:
    st.session_state.chat = []
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None
if "summary_cache" not in st.session_state:
    # keyed by (patient_id, persona)
    st.session_state.summary_cache = {}
if "last_action" not in st.session_state:
    st.session_state.last_action = None
if "selected_persona" not in st.session_state:
    st.session_state.selected_persona = "clinical"

SPECIALTY_EMOJI = {
    "general_practice": "🩺",
    "psychiatry": "🧠",
    "cardiology": "❤️",
    "endocrinology": "🧪",
    "dermatology": "🩹",
    "sleep_medicine": "😴",
}

DEMO_PROMPTS = {
    "clinical": [
        "What's driving her poor sleep and what's the plan?",
        "Are her cardiac symptoms benign or do they need escalation?",
        "Why is her HbA1c climbing despite metformin?",
        "Should we have caught this earlier?",
    ],
    "patient": [
        "What's been going on with my sleep?",
        "Why has my blood sugar been getting worse?",
        "Should I be worried about my heart?",
        "What's the most important thing for me to do this week?",
    ],
}

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("View")
persona_label = st.sidebar.radio(
    "Persona",
    options=["clinical", "patient"],
    format_func=lambda p: f"🩺 GP view" if p == "clinical" else "🧑 Patient view",
    key="selected_persona",
)

st.sidebar.markdown("---")
st.sidebar.header("Patient")
patients = list_patients(limit=20)
if not patients:
    st.warning("No patients on file. Run `python -m data.generate` first.")
    st.stop()
patient_options = {f"{p['_id']} — {p['name']}": p["_id"] for p in patients}
patient_label = st.sidebar.selectbox("Select patient", list(patient_options.keys()))
patient_id = patient_options[patient_label]

st.sidebar.markdown("---")
st.sidebar.header("Demo controls")
add_clicked = st.sidebar.button(
    "➕ Add follow-up visits",
    use_container_width=True,
    help="Drops 4 hand-authored follow-up visits into the record (incl. one novel specialty: sleep_medicine).",
)
reset_clicked = st.sidebar.button(
    "↺ Reset to seed",
    use_container_width=True,
    help="Removes the follow-up batch so the demo opens clean again.",
)

st.sidebar.markdown("---")
st.sidebar.header("Demo prompts")
st.sidebar.caption("Click to ask. Same flow as typing in the chat.")
for p in DEMO_PROMPTS[persona_label]:
    if st.sidebar.button(p, key=f"demo::{persona_label}::{p}", use_container_width=True):
        st.session_state.pending_query = p
        st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("🧹 Clear chat", use_container_width=True):
    st.session_state.chat = []
    st.rerun()


# ---------------------------------------------------------------------------
# Action handlers (run before render so the page reflects the new state)
# ---------------------------------------------------------------------------
def _invalidate_summary(pid: str):
    keys = [k for k in st.session_state.summary_cache if k[0] == pid]
    for k in keys:
        del st.session_state.summary_cache[k]


if add_clicked:
    with st.spinner("Adding follow-up visits and re-embedding..."):
        inserted = add_followup_batch(patient_id)
    _invalidate_summary(patient_id)
    st.session_state.last_action = (
        f"Added {len(inserted)} visits — `sleep_medicine` is new to the record. "
        f"Summary refreshed."
    )
    st.rerun()

if reset_clicked:
    n = remove_followup_batch()
    _invalidate_summary(patient_id)
    st.session_state.last_action = f"Removed {n} follow-up visit(s). Back to the seed corpus."
    st.rerun()

# ---------------------------------------------------------------------------
# Patient header
# ---------------------------------------------------------------------------
patient = get_patient(patient_id)
if not patient:
    st.error(f"Patient {patient_id} not found.")
    st.stop()

hdr1, hdr2, hdr3, hdr4 = st.columns([2, 1, 1, 1])
hdr1.markdown(f"### {patient['name']}")
hdr1.caption(f"{patient['_id']} · {patient.get('ethnicity', '')} · {patient.get('occupation', '')}")
hdr2.metric("Sex / Age", f"{patient.get('sex', '?')} / {patient.get('date_of_birth_age_years', '?')}")
hdr3.metric("BMI", patient.get("bmi", "?"))
hdr4.metric("Primary GP", patient.get("primary_gp", "?"))

with st.expander("PHI fields (decrypted on read; ciphertext at rest)"):
    st.write({
        "national_id": patient.get("national_id"),
        "dob": patient.get("dob"),
        "insurance_id": patient.get("insurance_id"),
    })
    st.caption(
        "These three fields are stored as AES-256-GCM ciphertext envelopes in MongoDB. "
        "The application decrypts them only for authorised reads. In production, use MongoDB "
        "Queryable Encryption to push this pattern into the database itself."
    )

if st.session_state.last_action:
    st.success(st.session_state.last_action)
    st.session_state.last_action = None

# ---------------------------------------------------------------------------
# AI overview card
# ---------------------------------------------------------------------------
st.markdown("---")
sum_col, btn_col = st.columns([4, 1])
sum_col.subheader("📋 AI overview")
refresh_clicked = btn_col.button("🔄 Refresh summary", use_container_width=True)

cache_key = (patient_id, persona_label)
if refresh_clicked and cache_key in st.session_state.summary_cache:
    del st.session_state.summary_cache[cache_key]

if cache_key not in st.session_state.summary_cache:
    with st.spinner(f"Generating {PERSONAS[persona_label]['label']} overview..."):
        st.session_state.summary_cache[cache_key] = summarize_patient(patient_id, persona_label)

summary = st.session_state.summary_cache[cache_key]

with st.container(border=True):
    st.write(summary["summary"] or "_No summary generated._")
    if summary["focus_areas"]:
        st.markdown(f"**{summary['focus_label']}**")
        for f in summary["focus_areas"]:
            st.markdown(f"- {f}")
    meta = (
        f"Generated {summary['generated_at'].strftime('%H:%M:%S UTC')} · "
        f"{summary['visit_count']} visits in record · "
        f"retrieval {summary['retrieval_ms']:.0f} ms · generation {summary['generation_ms']:.0f} ms"
    )
    st.caption(meta)

# ---------------------------------------------------------------------------
# Visit timeline
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("🗂️ Visit timeline")
visits = sorted(get_visits_for_patient(patient_id), key=lambda v: v.get("visit_date"), reverse=True)
if not visits:
    st.info("No visits on file.")
else:
    for v in visits:
        emoji = SPECIALTY_EMOJI.get(v.get("specialty"), "📄")
        date = v.get("visit_date")
        date_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)
        header = (
            f"{emoji}  **{date_str}** · {v.get('specialty', '?').replace('_', ' ').title()} · "
            f"{v.get('provider', '')} · _{v.get('chief_complaint', '')}_"
        )
        with st.expander(header):
            st.write(v.get("body", ""))
            structured = {
                k: v[k]
                for k in v
                if k not in {"_id", "patient_id", "visit_date", "specialty", "provider",
                             "chief_complaint", "body", "embedding", "last_updated"}
                and v[k] is not None
            }
            if structured:
                st.markdown("**Structured fields**")
                st.json(structured, expanded=False)
            st.caption(f"`{v['_id']}`")

# ---------------------------------------------------------------------------
# Q&A chat
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("💬 Ask about this record")
st.caption(
    f"Question scoped to {patient['name']} via patient-filtered `$vectorSearch`. "
    f"Answers tuned for the **{PERSONAS[persona_label]['label']}** persona."
)


def run_query(query: str):
    st.session_state.chat.append({"role": "user", "content": query})
    with st.spinner("Retrieving and answering..."):
        result = answer_question(patient_id, query, persona_label)
    st.session_state.chat.append({
        "role": "assistant",
        "content": result["answer"],
        "sources": result["sources"],
        "retrieval_ms": result["retrieval_ms"],
        "generation_ms": result["generation_ms"],
    })


for entry in st.session_state.chat:
    with st.chat_message(entry["role"]):
        st.write(entry["content"])
        if entry["role"] == "assistant":
            meta = []
            if "retrieval_ms" in entry:
                meta.append(f"retrieval {entry['retrieval_ms']:.0f} ms")
            if "generation_ms" in entry:
                meta.append(f"generation {entry['generation_ms']:.0f} ms")
            if meta:
                st.caption(" · ".join(meta))
            if entry.get("sources"):
                with st.expander(f"Sources ({len(entry['sources'])})"):
                    for s in entry["sources"]:
                        sd = s.get("visit_date")
                        sd_str = sd.strftime("%Y-%m-%d") if hasattr(sd, "strftime") else str(sd)
                        st.write(
                            f"- **{s['id']}** · {sd_str} · {s.get('specialty', '?')} "
                            f"· similarity `{s.get('score', 0):.4f}`"
                        )

typed = st.chat_input(f"Ask about {patient['name']}'s record...")
if typed:
    st.session_state.pending_query = typed
    st.rerun()

if st.session_state.pending_query:
    q = st.session_state.pending_query
    st.session_state.pending_query = None
    run_query(q)
    st.rerun()
