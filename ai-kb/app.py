"""Streamlit UI for the ai-kb PoC.

Split-pane layout that makes the "edit a doc → re-ask → answer changes" demo
moment land in front of an audience:
  - LEFT: chat with the knowledge base, sources expandable per turn
  - RIGHT: editor for any source document; saving re-embeds on the spot

Run from the project root (ai-kb/):
    streamlit run app.py
"""
from datetime import datetime, timezone

import streamlit as st

from src.docs import get_document, list_documents, upsert_document
from src.rag import answer_question

st.set_page_config(page_title="Self-Updating RAG", layout="wide")
st.title("📚 Self-Updating Knowledge Base")
st.caption("MongoDB Atlas + Voyage AI: edits propagate immediately. No sync pipeline.")

if "chat" not in st.session_state:
    st.session_state.chat = []
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None
if "last_save" not in st.session_state:
    st.session_state.last_save = None

# ---------------------------------------------------------------------------
# Sidebar: pre-baked demo prompts
# ---------------------------------------------------------------------------
DEMO_PROMPTS = [
    "How much parental leave do we offer?",
    "What's our remote work policy?",
    "How do I reset my VPN access?",
    "What's the limit on my corporate card?",
    "How do I report a phishing email?",
]

st.sidebar.header("Demo prompts")
st.sidebar.caption("Click to ask. Same flow as typing in the chat.")
for p in DEMO_PROMPTS:
    if st.sidebar.button(p, key=f"demo::{p}", use_container_width=True):
        st.session_state.pending_query = p
        st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("🧹 Clear chat", use_container_width=True):
    st.session_state.chat = []
    st.rerun()


def run_query(query: str):
    st.session_state.chat.append({"role": "user", "content": query})
    with st.spinner("Retrieving and answering..."):
        result = answer_question(query)
    st.session_state.chat.append({
        "role": "assistant",
        "content": result["answer"],
        "sources": result["sources"],
        "retrieval_ms": result["retrieval_ms"],
        "generation_ms": result["generation_ms"],
    })


col_chat, col_edit = st.columns([1, 1])

# ===========================================================================
# LEFT: Chat
# ===========================================================================
with col_chat:
    st.subheader("💬 Ask the knowledge base")

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
                            st.write(
                                f"- **{s['id']}** — {s['title']} "
                                f"_({s['category']})_ &nbsp;·&nbsp; "
                                f"similarity `{s['score']:.4f}`"
                            )

    typed = st.chat_input("Ask a question...")
    if typed:
        st.session_state.pending_query = typed
        st.rerun()

    if st.session_state.pending_query:
        q = st.session_state.pending_query
        st.session_state.pending_query = None
        run_query(q)
        st.rerun()

# ===========================================================================
# RIGHT: Document editor
# ===========================================================================
with col_edit:
    st.subheader("✏️ Source documents")
    st.caption("Saving re-embeds the document immediately — no sync pipeline.")

    docs = list_documents()
    if not docs:
        st.warning("Knowledge base is empty. Run `python -m data.generate` first.")
        st.stop()

    doc_options = {f"[{d['category']}] {d['_id']} — {d['title']}": d["_id"] for d in docs}
    labels = list(doc_options.keys())

    # Default to hr-001 if present (the parental-leave demo target)
    default_idx = next(
        (i for i, lbl in enumerate(labels) if doc_options[lbl] == "hr-001"),
        0,
    )
    selected_label = st.selectbox("Document", labels, index=default_idx)
    selected_id = doc_options[selected_label]

    current = get_document(selected_id)
    if not current:
        st.error(f"Document {selected_id} not found.")
        st.stop()

    new_title = st.text_input("Title", value=current["title"], key=f"title::{selected_id}")
    new_body = st.text_area("Body", value=current["body"], height=320, key=f"body::{selected_id}")
    new_category = st.text_input("Category", value=current["category"], key=f"cat::{selected_id}")

    last_updated = current.get("last_updated")
    if last_updated:
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - last_updated).total_seconds()
        st.caption(f"Last updated: {last_updated.isoformat(timespec='seconds')} ({age_s:.0f}s ago)")

    save_col, info_col = st.columns([1, 2])
    with save_col:
        save_clicked = st.button("💾 Save & re-embed", type="primary", use_container_width=True)
    with info_col:
        if st.session_state.last_save:
            st.success(st.session_state.last_save)

    if save_clicked:
        with st.spinner("Re-embedding via voyage-3 and saving..."):
            upsert_document(selected_id, new_title, new_body, new_category)
        st.session_state.last_save = (
            f"Saved {selected_id} — embedding regenerated. "
            f"Ask the same question again to see the new answer."
        )
        st.rerun()
