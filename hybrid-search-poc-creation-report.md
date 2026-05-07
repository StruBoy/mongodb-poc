# Hybrid-Search PoC — Session Report

## Starting state
- `mongodb_poc/` had 5 empty PoC scaffolds + `plans/` with playbook docs for each
- `hybrid-search/` had 0-byte stubs for every Python file
- No working pipeline, indexes, UI, or documentation

## What got built

### Modules (`src/`)
| File | Purpose |
|---|---|
| `db.py` | Lazy-singleton `MongoClient` with `tlsCAFile=certifi.where()` for macOS Python |
| `embed.py` | `embed_texts(...)` via `voyage-3` (1024-dim) + `product_to_text(...)` |
| `core.py` | Three search modes: `keyword_search` (Atlas Search), `semantic_search` (Vector Search), `hybrid_search` (native `$rankFusion`, MongoDB 8.1+) |

### Scripts (`scripts/`, run via `python -m scripts.<name>`)
| Script | What it does |
|---|---|
| `check_env.py` | 6-stage check: env vars, MongoDB ping, db/collection presence, Voyage auth, Voyage **billing probe** (4 rapid calls to detect free-tier 3 RPM cap), Anthropic auth |
| `create_db_index.py` | Phase 1.3 (db + collection) and Phase 1.4 (text + vector indexes); idempotent; polls until queryable |
| `verify_catalog.py` | Post-generate integrity check: count, required fields, embedding dim, category/brand coverage, description-quality heuristic |
| `smoke_test.py` | Runs all three search modes against the marathon-shoes demo query; exits non-zero if any returns 0 results |

### Data pipeline (`data/`)
- `generate.py` — ~2,500 product skeletons across 4 categories × 5 types × 5 brands; Claude (`claude-haiku-4-5`) writes descriptions in batches of 25; Voyage embeds in batches of 50; one `insert_many` at the end. Hardened beyond the playbook with a fallback description so LLM truncation doesn't crash later embedding.

### UI (`app.py`)
Streamlit app with two tabs:
- **🔍 Search compare** — three side-by-side panes for keyword / semantic / hybrid; sidebar has three pre-baked demo queries (marathon, brand search, long-flights)
- **📋 Full catalog** — sortable `st.dataframe` of all products with category/brand/text/price-range/rating filters; data cached for 5 min

### Documentation
- `README.md` at project root — 8-step deploy walkthrough, prerequisites, layout diagram, cleanup checklist

## Problems we hit and fixed

| Symptom | Root cause | Fix |
|---|---|---|
| `CERTIFICATE_VERIFY_FAILED` on Mongo connect | macOS Python lacks system root CAs | `tlsCAFile=certifi.where()` everywhere |
| `ModuleNotFoundError: faker` during `generate.py` | Missing from playbook's pip line | Added to `requirements.txt` |
| `RateLimitError: ... payment method` | Voyage free tier capped at 3 RPM | User added a card; verified via the new `check_voyage_billing()` probe |
| Hybrid `$rankFusion`: `Unrecognized pipeline stage` | Cluster on 8.0.23 | User upgraded the Atlas cluster to 8.1+ (now on 8.3.2) |
| Hybrid: `query requires scoreDetails metadata` | Need `scoreDetails: true` on the stage | Switched to `$addFields {score: $meta:"score"}` for the simpler RRF score |
| Streamlit metric truncating prices to `$22…` | `st.metric` font is too large for narrow column | Replaced metric tiles with one-line markdown footer |

## Reorganization
- Moved `check_env`, `verify_catalog`, `smoke_test`, and `create_db_index` from `src/` into a new `scripts/` directory — clean boundary between *code the app needs* and *things you run from the terminal*.
- Renamed `create_indexes.py` → `create_db_index.py`; restructured to label Phase 1.3 (db setup) and Phase 1.4 (indexes) explicitly per the playbook.

## Final layout
```
mongodb_poc/
├── README.md              ← deployment guide
├── plans/                 ← 5 PoC playbooks
└── hybrid-search/
    ├── app.py             ← Streamlit UI (2 tabs)
    ├── requirements.txt   ← + faker, certifi
    ├── .env.example
    ├── src/               ← db.py, embed.py, core.py
    ├── scripts/           ← check_env, create_db_index, verify_catalog, smoke_test
    └── data/              ← generate.py
```

## Run book (recap)
```bash
cd hybrid-search && source venv/bin/activate
python -m scripts.check_env         # 6/6 OK
python -m scripts.create_db_index   # phase 1.3 + 1.4
python -m data.generate             # ~10 min, ~USD 0.40
python -m scripts.verify_catalog    # ~2500 docs, 1024-dim embeddings
python -m scripts.smoke_test        # all three modes return results
streamlit run app.py                # localhost:8501
```

## Outstanding
- `MONGODB_URI` in `.env` currently NXDOMAINs (`poc-hybrid-search.iwsfawz.mongodb.net`) — needs to be re-copied from Atlas before further runs
- The other four PoC directories (`fraud-detect/`, `ai-kb/`, `govtech-casemgmt/`, `iot-telemetry/`) are still empty; their playbooks under `plans/` are ready when you want to build any of them
