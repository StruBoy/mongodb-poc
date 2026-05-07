# AI-KB PoC — Session Report

## Starting state
- `mongodb_poc/` had `hybrid-search/` and `fraud-detect/` fully deployed; `ai-kb/` was an empty directory
- Playbook `plans/03_AI_RAG_Knowledge_Base.md` describes a 20-document enterprise corpus across 5 categories with split-pane Streamlit chat + editor
- Atlas cluster `poc-cluster` already provisioned (M10, 8.3.2) and shared with the other two PoCs; credentials in `secrets.txt` and reused `.env` values

## What got built

### Modules (`src/`)
| File | Purpose |
|---|---|
| `db.py` | Lazy-singleton `MongoClient` with `tlsCAFile=certifi.where()` (mirrors hybrid-search and fraud-detect) — points at `kb_demo` |
| `embed.py` | `embed_texts(...)` via `voyage-3` (1024-dim) + `doc_to_text(...)` (title + double-newline + body) |
| `docs.py` | `upsert_document` / `get_document` / `list_documents` — **the architectural piece**: every upsert re-embeds via voyage-3 on the same write path, used by both the seed generator and the editor UI |
| `rag.py` | `retrieve_context` ($vectorSearch, optional category filter) + `answer_question` (Claude haiku 4.5 with retrieval/generation latency timings) |

### Scripts (`scripts/`, run via `python -m scripts.<name>`)
| Script | What it does |
|---|---|
| `check_env.py` | 6-stage check: env vars, MongoDB ping, `kb_demo.documents` reachability, Voyage auth, Voyage billing probe (4 rapid calls to detect free-tier 3 RPM cap), Anthropic auth |
| `create_db_index.py` | Phase 1.3 (db + collection) and Phase 1.4 (`kb_vector_idx` — 1024-dim cosine + `category` filter); idempotent; polls until queryable |
| `verify_corpus.py` | 6 checks: count (20), required fields, embedding dim, category distribution, `_id` string format, body-length floor for fallback detection |
| `smoke_test.py` | Three representative questions through `answer_question` — parental leave, remote work, phishing reporting — asserts the expected top source ID |

### Data pipeline (`data/`)
- `generate.py` — clears `kb_demo.documents`, walks the 20-tuple `DOCUMENT_SPECS` from the playbook, expands each via Claude haiku 4.5 (single call per doc, ~150–200 word body, JSON-mode prompt with fallback templating on parse error), and persists through `src.docs.upsert_document` so the seed and editor share one code path. ~2 min total, well under USD 0.10.

### UI (`app.py`)
Streamlit split-pane:
- **Left**: chat history with sources expander per turn (similarity score, category, retrieval/generation latency); `st.chat_input` plus a sidebar of five demo prompts that all flow through the same `pending_query` → `run_query` path (no duplicated logic)
- **Right**: document selector (defaults to `hr-001` so the parental-leave demo is one click away), title/body/category text fields, "💾 Save & re-embed" button, last-updated timestamp with age in seconds
- **Sidebar**: 5 pre-baked prompts + clear-chat button

### Documentation
- Updated root `README.md`: flipped status row to **Implemented**, added the 8-step deploy walkthrough + 5-beat demo flow + project-layout entry

## Problems we hit and fixed

This session was largely on-rails — the fraud-detect tuning saga didn't repeat here because RAG over distinct policy documents is a clean voyage-3 cosine job. The two real items:

| Symptom | Root cause | Fix |
|---|---|---|
| `__init__.py` files added unnecessarily to `src/`, `scripts/`, `data/` | Habit; not how the other two PoCs are laid out | Removed all three after the user flagged it. Python 3 namespace packages (PEP 420) handle `python -m scripts.foo` and `from src.db import ...` without them; verified imports still work |
| Need to confirm the live-edit demo *actually* propagates | Couldn't claim the demo works without testing it | One-shot script: edited `hr-001` body to swap "twelve weeks" → "16 weeks", re-ran `answer_question`, confirmed the answer flipped from "twelve weeks" to "**16 weeks**" with no index rebuild step. Restored to twelve weeks so the demo opens clean |

## Smoke-test results (final corpus, threshold-free)

| Question | Expected top | Got | Score | Latency |
|---|---|---|---|---|
| "How much parental leave do we offer?" | `hr-001` | `hr-001` | 0.7537 | 597 ms retrieve + 1553 ms gen |
| "What's our remote work policy?" | `hr-004` | `hr-004` | 0.7693 | 253 ms + 2284 ms |
| "How do I report a phishing email?" | `sec-003` | `sec-003` | 0.8001 | 305 ms + 1783 ms |

Top-1 was correct in every case with a clean ~0.10–0.15 margin over the runner-up. No tuning required.

## Deviations from the playbook

Minimal — the playbook spec held up well.

- **`upsert_document` returns the saved payload** (minus the embedding) so the editor can show "saved at" feedback without a re-read round-trip
- **`answer_question` returns `retrieval_ms` and `generation_ms`** so the chat caption can show latency per turn — useful when explaining why the second click of the same prompt is faster
- **`category` filter parameter on `retrieve_context`** is wired but the UI doesn't expose it; ready if you want to add a category dropdown to the chat input
- **Empty-corpus and empty-retrieval guards** in `answer_question` and `app.py` so the UI doesn't crash before the first generation run
- **`upsert_document` uses timezone-aware UTC** (`datetime.now(timezone.utc)`) instead of the playbook's deprecated `datetime.utcnow()`

## Final layout
```
ai-kb/
├── app.py                 ← Streamlit split-pane (chat | editor)
├── requirements.txt
├── .env / .env.example
├── src/
│   ├── db.py
│   ├── embed.py
│   ├── docs.py            ← upsert_document re-embeds on every write
│   └── rag.py
├── scripts/
│   ├── check_env.py
│   ├── create_db_index.py
│   ├── verify_corpus.py
│   └── smoke_test.py
└── data/
    └── generate.py
```

## Run book (recap)
```bash
cd ai-kb && source venv/bin/activate
python -m scripts.check_env         # 6/6 OK
python -m scripts.create_db_index   # idempotent
python -m data.generate             # ~2 min, ~USD 0.05
python -m scripts.verify_corpus     # 20 docs, 1024-dim embeddings, 5 categories
python -m scripts.smoke_test        # 3 questions, top source verified
streamlit run app.py                # localhost:8501
```

## Outstanding
- The plan calls out Atlas Vector Search's **native auto-embedding via Voyage AI** (preview) as an alternative to the application-side pattern used here. Worth flagging to the audience but not switching to — the application pattern is more portable across MongoDB versions and clearer to demonstrate (the embed call is right there in `src/docs.py`).
- The `category` filter on `retrieve_context` is plumbed but the UI doesn't expose it; a category dropdown next to the chat input would land the "filtered semantic search" point if asked.
- The two remaining PoCs (`govtech-casemgmt/`, `iot-telemetry/`) are still empty scaffolds with their playbooks ready under `plans/`.
