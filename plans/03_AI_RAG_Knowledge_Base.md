# PoC 3: Self-Updating RAG Knowledge Base — Implementation Plan

**Customer Segment:** AI-Native Startups & Enterprises Modernizing Legacy Systems
**Time Budget:** 3 hours
**Cluster Tier:** Atlas M10 (Vector Search required)
**Total Cost (afternoon):** ~USD 4

---

## What you're building

A retrieval-augmented chatbot over a synthetic enterprise knowledge base, where source-of-truth document edits propagate to the AI answer **without any pipeline running**. Streamlit serves both the chat interface and a side-by-side document editor so you can demonstrate the live-update moment in front of an audience.

## What it demonstrates

- **No synchronization tax**: edit a source document, ask the question again, get the new answer
- **Native auto-embedding**: Voyage AI embedding triggered automatically on document write
- **Production-ready RAG architecture** in one platform, not four
- **Citation-ready answers** that show which documents the model used

## The demo moment

The chatbot answers "What is the parental leave policy?" with "12 weeks of paid leave." You edit the source document on screen — change 12 to 16. Ask the same question. The answer is now "16 weeks." No pipeline ran. No re-embedding job. The audience sees the synchronization tax simply disappear.

---

## Prerequisites

- Atlas account with cluster permissions
- Voyage AI API key
- Anthropic API key (for chat response generation)
- Python 3.11+

---

## Phase 1: Atlas Setup (30 min)

### 1.1 Provision the cluster

1. Create project `poc-rag-knowledge`
2. Build cluster: M10, AWS, Singapore region
3. Wait ~7 minutes

### 1.2 Configure access

- Database user `pocuser` with `readWriteAnyDatabase`
- Network access from your IP

### 1.3 Create the database

```javascript
use kb_demo
db.createCollection("documents")
```

### 1.4 Define the vector search index

`kb_demo.documents`, name `kb_vector_idx`:

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1024,
      "similarity": "cosine"
    },
    {
      "type": "filter",
      "path": "category"
    }
  ]
}
```

---

## Phase 2: Project Setup & Document Corpus (45 min)

### 2.1 Project scaffolding

```bash
mkdir poc-rag-knowledge && cd poc-rag-knowledge
python -m venv venv && source venv/bin/activate
pip install pymongo voyageai anthropic streamlit python-dotenv
```

`.env`:

```
MONGODB_URI=mongodb+srv://pocuser:<password>@<cluster>.mongodb.net/
VOYAGE_API_KEY=pa-xxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxx
```

### 2.2 Database helper (`src/db.py`)

```python
import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
_client = None

def get_client():
    global _client
    if _client is None:
        _client = MongoClient(os.environ["MONGODB_URI"])
    return _client

def get_db():
    return get_client()["kb_demo"]
```

### 2.3 Embedding helper with auto-trigger (`src/embed.py`)

The key architectural pattern: every write to `documents` triggers re-embedding. We implement this in application code (the cleanest cross-version approach), but Atlas Vector Search now also supports native auto-embedding via Voyage AI in preview — flag this to the audience.

```python
import os
import voyageai
from dotenv import load_dotenv

load_dotenv()
vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])

def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    result = vo.embed(texts, model="voyage-3", input_type=input_type)
    return result.embeddings

def doc_to_text(doc: dict) -> str:
    return f"{doc['title']}\n\n{doc['body']}"
```

### 2.4 Document service with auto-embedding (`src/docs.py`)

This is the core architectural piece. Every `upsert_document` call regenerates the embedding — that's what makes the live-update demo work.

```python
from datetime import datetime
from src.db import get_db
from src.embed import embed_texts, doc_to_text


def upsert_document(doc_id: str, title: str, body: str, category: str):
    """Create or update a document. The embedding regenerates on every write."""
    db = get_db()
    payload = {
        "title": title,
        "body": body,
        "category": category,
        "last_updated": datetime.utcnow()
    }
    [embedding] = embed_texts([doc_to_text(payload)], input_type="document")
    payload["embedding"] = embedding
    db.documents.update_one(
        {"_id": doc_id},
        {"$set": payload},
        upsert=True
    )


def get_document(doc_id: str):
    db = get_db()
    return db.documents.find_one({"_id": doc_id}, {"embedding": 0})


def list_documents():
    db = get_db()
    return list(db.documents.find({}, {"embedding": 0}).sort("category", 1))
```

### 2.5 Initial corpus generator (`data/generate.py`)

```python
import os
import json
from anthropic import Anthropic
from src.docs import upsert_document
from dotenv import load_dotenv

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DOCUMENT_SPECS = [
    # (id, category, topic, key_facts)
    ("hr-001", "HR", "Parental Leave Policy",
     "12 weeks paid leave; applies after 6 months tenure; both parents eligible"),
    ("hr-002", "HR", "Annual Leave Entitlement",
     "20 working days per year; pro-rated for part-time; carry-over up to 5 days"),
    ("hr-003", "HR", "Sick Leave Policy",
     "10 paid sick days per year; medical certificate required after 3 consecutive days"),
    ("hr-004", "HR", "Remote Work Policy",
     "Up to 3 days remote per week; team lead approval required; quarterly review"),
    ("hr-005", "HR", "Performance Review Cycle",
     "Annual cycle in March; 360-degree feedback; calibration meetings in April"),
    ("it-001", "IT", "VPN Setup Guide",
     "Cisco AnyConnect; download from internal portal; MFA via Authy"),
    ("it-002", "IT", "Password Policy",
     "Minimum 14 characters; rotation every 90 days; password manager required"),
    ("it-003", "IT", "Laptop Refresh Policy",
     "MacBook Pro every 3 years; option to keep old laptop for AUD 200"),
    ("it-004", "IT", "Software Procurement",
     "Submit ticket via ServiceNow; auto-approved under AUD 500; security review for SaaS"),
    ("it-005", "IT", "Incident Response Procedure",
     "Page on-call via PagerDuty; severity 1 within 15 minutes; postmortem within 5 days"),
    ("fin-001", "Finance", "Expense Reimbursement",
     "Submit via Concur within 30 days; receipts required over AUD 50; manager approval"),
    ("fin-002", "Finance", "Travel Booking Policy",
     "Book via Egencia; economy domestic; business class for flights over 6 hours"),
    ("fin-003", "Finance", "Corporate Card Usage",
     "AmEx Business; AUD 5000 monthly limit; personal use prohibited"),
    ("fin-004", "Finance", "Vendor Onboarding",
     "Submit W-9 equivalent; AP team approval; 30-day net payment terms"),
    ("prod-001", "Product", "Release Process",
     "Two-week sprints; release notes via Notion; staging soak for 24 hours"),
    ("prod-002", "Product", "Feature Flag Policy",
     "All new features behind LaunchDarkly flags; default off; gradual rollout"),
    ("prod-003", "Product", "Customer Escalation Path",
     "Tier 1 to Tier 2 within 4 hours; engineer escalation via Slack #escalations"),
    ("sec-001", "Security", "Data Classification",
     "Public, Internal, Confidential, Restricted; PII is Restricted by default"),
    ("sec-002", "Security", "Access Review Cadence",
     "Quarterly access review; auto-revoke after 90 days inactivity; SOC 2 audit annually"),
    ("sec-003", "Security", "Phishing Reporting",
     "Forward to phishing@company.com; do not click links; expect response within 2 hours"),
]


def generate_full_document(topic: str, key_facts: str) -> tuple[str, str]:
    """Use Claude to expand a topic + facts into a realistic 200-word policy document."""
    prompt = f"""Generate a realistic enterprise policy document.

Topic: {topic}
Key facts to include: {key_facts}

Requirements:
- Title: clear and direct, matching the topic
- Body: 150-200 words, in the voice of a corporate HR/IT/Finance team
- Include the key facts naturally; do not bullet-point them
- End with a contact point or escalation path
- Sound like a real internal document, not marketing copy

Return as JSON: {{"title": "...", "body": "..."}}"""

    response = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text.strip())
    return parsed["title"], parsed["body"]


def main():
    for doc_id, category, topic, key_facts in DOCUMENT_SPECS:
        print(f"Generating {doc_id} ({topic})...")
        try:
            title, body = generate_full_document(topic, key_facts)
        except Exception as e:
            print(f"  Failed: {e}; using fallback.")
            title = topic
            body = f"Policy for {topic}. Key details: {key_facts}. Contact HR for questions."
        upsert_document(doc_id, title, body, category)
    print("Corpus loaded.")


if __name__ == "__main__":
    main()
```

Run it:

```bash
python -m data.generate
```

Takes ~3 minutes. Wait an additional minute for the vector index to populate after document inserts.

---

## Phase 3: RAG Pipeline (45 min)

### 3.1 Retrieval and generation (`src/rag.py`)

```python
import os
from anthropic import Anthropic
from src.db import get_db
from src.embed import embed_texts
from dotenv import load_dotenv

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def retrieve_context(query: str, k: int = 4):
    db = get_db()
    [embedding] = embed_texts([query], input_type="query")
    pipeline = [
        {
            "$vectorSearch": {
                "index": "kb_vector_idx",
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": 50,
                "limit": k
            }
        },
        {
            "$project": {
                "_id": 1,
                "title": 1,
                "body": 1,
                "category": 1,
                "last_updated": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    return list(db.documents.aggregate(pipeline))


def answer_question(query: str) -> dict:
    docs = retrieve_context(query, k=4)

    context_block = "\n\n---\n\n".join(
        f"[Document {d['_id']}: {d['title']}]\n{d['body']}"
        for d in docs
    )

    prompt = f"""You are an internal company assistant. Answer the user's question using ONLY the documents provided below.

If the answer isn't in the documents, say so. Cite the document IDs you used.

Documents:
{context_block}

Question: {query}

Answer:"""

    response = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}]
    )

    return {
        "answer": response.content[0].text,
        "sources": [{"id": d["_id"], "title": d["title"], "score": d["score"]} for d in docs]
    }
```

### 3.2 Quick smoke test

```python
# scripts/test_rag.py
from src.rag import answer_question

result = answer_question("How much parental leave do we offer?")
print(result["answer"])
print("\nSources:")
for s in result["sources"]:
    print(f"  - {s['id']}: {s['title']} (score: {s['score']:.3f})")
```

You should get an answer mentioning 12 weeks, with `hr-001` cited as the top source.

---

## Phase 4: Streamlit Chat + Editor UI (60 min)

### 4.1 The dual-pane interface (`app.py`)

This is the most important code in the whole PoC. The split-screen layout is what makes the live-update moment land.

```python
import streamlit as st
from src.rag import answer_question
from src.docs import upsert_document, get_document, list_documents

st.set_page_config(page_title="Self-Updating RAG", layout="wide")
st.title("📚 Self-Updating Knowledge Base")
st.caption("MongoDB Atlas + Voyage AI: edits propagate immediately. No pipelines.")

if "chat" not in st.session_state:
    st.session_state.chat = []

col_chat, col_edit = st.columns([1, 1])

# ============================================================
# LEFT: Chat
# ============================================================
with col_chat:
    st.subheader("💬 Ask the knowledge base")

    for entry in st.session_state.chat:
        with st.chat_message(entry["role"]):
            st.write(entry["content"])
            if entry["role"] == "assistant" and "sources" in entry:
                with st.expander("Sources"):
                    for s in entry["sources"]:
                        st.write(f"- **{s['id']}**: {s['title']} (similarity: {s['score']:.3f})")

    query = st.chat_input("Ask a question...")
    if query:
        st.session_state.chat.append({"role": "user", "content": query})
        with st.spinner("Retrieving and answering..."):
            result = answer_question(query)
        st.session_state.chat.append({
            "role": "assistant",
            "content": result["answer"],
            "sources": result["sources"]
        })
        st.rerun()

# ============================================================
# RIGHT: Document editor
# ============================================================
with col_edit:
    st.subheader("✏️ Source documents")
    st.caption("Editing a document re-embeds it immediately.")

    docs = list_documents()
    doc_options = {f"{d['_id']} — {d['title']}": d["_id"] for d in docs}
    selected_label = st.selectbox("Select document", list(doc_options.keys()))
    selected_id = doc_options[selected_label]

    current = get_document(selected_id)

    new_title = st.text_input("Title", value=current["title"])
    new_body = st.text_area("Body", value=current["body"], height=300)
    new_category = st.text_input("Category", value=current["category"])

    if st.button("💾 Save changes", type="primary"):
        with st.spinner("Re-embedding and saving..."):
            upsert_document(selected_id, new_title, new_body, new_category)
        st.success(f"Document {selected_id} updated. New embedding generated.")
        st.balloons()
```

### 4.2 Launch

```bash
streamlit run app.py
```

---

## Phase 5: Demo Polish (30 min)

### 5.1 Pre-stage the demo

1. Open the app
2. Ask: "How much parental leave do we offer?" — confirm answer is correct (12 weeks, cites hr-001)
3. Note the exact wording so you'll see the change clearly

### 5.2 Add a "demo prompts" sidebar

```python
# Add at top of app.py before the columns
st.sidebar.subheader("Demo prompts")
demo_prompts = [
    "How much parental leave do we offer?",
    "What's our remote work policy?",
    "How do I reset my VPN access?",
    "What's the limit on my corporate card?",
    "How do I report a phishing email?"
]
for p in demo_prompts:
    if st.sidebar.button(p, key=p):
        st.session_state.chat.append({"role": "user", "content": p})
        from src.rag import answer_question
        result = answer_question(p)
        st.session_state.chat.append({
            "role": "assistant",
            "content": result["answer"],
            "sources": result["sources"]
        })
        st.rerun()
```

### 5.3 Test the full live-edit sequence

1. Click "How much parental leave do we offer?" → answer mentions 12 weeks
2. In the right pane, select `hr-001`
3. In the body, change "12 weeks" to "16 weeks"
4. Click Save
5. Click the parental leave question again
6. New answer should mention 16 weeks

If the answer doesn't update on the second query, the embedding hasn't been regenerated — check the `upsert_document` flow.

---

## Demo Script

**[1 min] Set up the universal RAG problem.**
"Every team building production RAG hits the same wall. Source documents update — a policy changes, a product spec evolves, a new regulation lands — and the embeddings in your vector database go stale. The standard fix is a CDC pipeline plus a re-embedding job plus monitoring for drift. That's the integration tax in one sentence."

**[1 min] Show the architecture.**
"This is one MongoDB cluster. Documents and their embeddings live in the same collection. When a document is written, its embedding regenerates as part of the same operation. There is no separate vector store. There is no synchronization pipeline."

**[1 min] Show the RAG working normally.**
Click the parental leave demo prompt. "We retrieved four documents by semantic similarity, and Claude answered using only those documents. Twelve weeks of paid leave."

**[2 min] The live-edit moment.**
"Now I'm going to do something most teams won't do live. I'm going to edit the source document right now."

Switch to the editor pane. Change "12 weeks" to "16 weeks." Click Save. "That save just regenerated the embedding for this document. No pipeline. No job. No re-indexing event."

Click the parental leave prompt again. New answer comes back: "16 weeks of paid leave."

**[30 sec] Land the architectural point.**
"What you just saw is what every team building production RAG is trying to achieve and almost none of them have today. The reason it works is that the operational store and the vector store are the same store. There's nothing to synchronize because there's nothing separate."

---

## Troubleshooting

**Answer doesn't change after editing a document.**
Confirm the `upsert_document` function is called and runs `embed_texts` before writing. Check `last_updated` is changing after each save. The vector index updates within a few seconds; if the answer still doesn't change, the chunk being retrieved may be a different document — check the cited sources.

**Claude returns "I don't see this in the documents" for valid queries.**
The retrieval is missing the relevant document. Lower `numCandidates` to ensure the right doc surfaces, or increase `k` to retrieve more context.

**Voyage AI calls fail intermittently.**
Add retry logic with exponential backoff:

```python
import time
def embed_texts_retry(texts, input_type="document", max_retries=3):
    for attempt in range(max_retries):
        try:
            return embed_texts(texts, input_type)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
```

**Streamlit re-runs cause duplicate chat entries.**
Use `st.session_state` keys for both the prompt button and the chat input to avoid double-submission.

---

## Cleanup

```bash
# Pause or terminate the Atlas cluster
deactivate
rm -rf poc-rag-knowledge
```

---

## Variations

- **Streaming responses**: switch the Claude call to streaming mode for a more polished chat feel
- **Multi-turn context**: maintain conversation history and pass it into the prompt for follow-up questions
- **Permission filtering**: add an `allowed_roles` field to documents; filter the vector search by the user's role
- **Citation highlighting**: highlight the specific sentences in the source documents that informed the answer

---

## Note on native auto-embedding

Atlas Vector Search now supports native auto-embedding via Voyage AI as a preview feature. If you'd rather have the database itself manage the embedding lifecycle (no `embed_texts` call in your application code), configure the auto-embedding option in the index definition and write only the source text — the embedding is generated automatically on insert/update. The application-level pattern shown here is more portable across MongoDB versions and clearer to demonstrate; the native pattern is cleaner for production.
