# Fraud-Detect PoC — Session Report

## Starting state
- `mongodb_poc/` had `hybrid-search/` fully deployed; `fraud-detect/` was an empty scaffold (just `.env.example`, empty `src/`, `scripts/`, `data/`)
- Playbook `plans/01_FinServ_Fraud_Detection.md` describes a single global `$vectorSearch` over 200 fraud examples (5 archetypes × 40), avg-of-top-3 cosine, threshold 0.78
- Atlas project `poc-fraud-detection` already provisioned (M10, 8.3.2); credentials in `secrets.txt`

## What got built

### Modules (`src/`)
| File | Purpose |
|---|---|
| `db.py` | Lazy-singleton `MongoClient` with `tlsCAFile=certifi.where()` (mirrors hybrid-search) |
| `embed.py` | `embed_texts(...)` via `voyage-3` (1024-dim) + `transaction_to_text(...)` — went through five iterations, settled on **tag-style with raw values** (`"large amount of 2400 dollars. online card not present. domestic AU. near home 8 km. middle of the night at 03:00."`) |
| `core.py` | `score_transaction(...)` — pivoted off the playbook's single-global-search approach to **per-archetype filtered `$vectorSearch`** with `numCandidates=500`, picking the best match across archetypes |

### Scripts (`scripts/`, run via `python -m scripts.<name>`)
| Script | What it does |
|---|---|
| `check_env.py` | 5-stage check: env vars, MongoDB ping, `fraud_demo` db / collections, Voyage auth, Voyage billing probe (no Anthropic — PoC uses faker, not Claude) |
| `create_db_index.py` | Creates db + both collections + `fraud_vector_idx` (1024-dim cosine, with `archetype` filter); idempotent; polls until queryable |
| `verify_data.py` | 9 checks across `fraud_examples` and `transactions`: counts, required fields, embedding shape, archetype distribution, label sanity, no stray embeddings on transactions |
| `smoke_test.py` | 3 hand-crafted transactions through `score_transaction` (foreign CNP, local groceries, off-hours large) |
| `calibrate.py` | Samples 100 random normals + 20 of each archetype; reports risk-score distribution, threshold sweep (FPR), and per-archetype TPR — drives the threshold choice |
| `probe.py` | Per-archetype diagnostic: runs filtered `$vectorSearch` against each archetype individually with `numCandidates=2000`, surfaces what the corpus *actually* says when the global search masks it |
| `_ping.py` | One-shot scoring probe used by `until`-loops while mongot is restarting |

### Data pipeline (`data/`)
- `generate.py` — clears both collections; produces 400 fraud examples × 3 archetypes (1,200 total), embeds in batches of 50; produces 100,000 normal transactions inserted in batches of 10,000. Total runtime ~45–50 s. Voyage cost negligible.

### UI (`app.py`)
Streamlit dashboard:
- **Sidebar:** archetype selector, 💉 Inject button, auto-stream toggle, stream-interval slider, reset, live session stats (total scored, flagged, flag rate)
- **Live feed:** every scored transaction with red/green badge, top archetype, gap over runner-up, end-to-end latency
- **Alerts:** expand each to see best match per archetype, margin, full transaction details, and a popover showing the exact text used as the embedding query

### Documentation
- Updated root `README.md` with the fraud-detect deployment section (9 steps) and a **Demo flow** subsection for both implemented PoCs

## Problems we hit and fixed

The session was largely a tuning saga driven by calibration data, not a checklist. Key inflection points:

| Symptom | Diagnosis | Fix |
|---|---|---|
| 76% of normal transactions flagged using the playbook's approach | voyage-3 cosine on the original natural-language template collapses everything into 0.83–0.87, where fraud and normal overlap completely | Switched to **per-archetype filtered `$vectorSearch`** + tag-style text with raw values |
| Foreign CNP query matched `account_takeover` (AU) instead of `foreign_cnp` (RU) at score 0.79 | With global `numCandidates=100`, foreign_cnp docs weren't surviving the candidate pool — voyage-3 considers AU corpus items more "generally similar" to anything | Per-archetype filter with `numCandidates=500` — surfaces best match *within* each archetype |
| Richer descriptive prose made the problem worse | Long phrases share more boilerplate → more shared embedding direction → narrower cosine spread | Reverted to terse tags |
| Symmetric `input_type="document"` on queries lifted cosine ceiling to ~1.0 but lifted normals along with fraud | The asymmetry only matters when fraud and normal are genuinely distinct in embedding space; with structural overlap it just shifts the band | Reverted |
| `amount_anomaly` and `merchant_category_fraud` archetypes wouldn't flag | Both archetypes only differed from normal transactions on the amount axis — voyage-3 cosine doesn't strongly separate `$50` from `$5000` when the rest of the text matches | Dropped both archetypes; final corpus has 3 |
| `mongot is shutting down` errors during calibration | Atlas Search service was overloaded by ~720 vector searches in rapid succession (4 per scoring call × 180 samples) | `_ping.py` + `until` loop in bash to wait for service recovery rather than retrying blindly |
| Consensus check (top-1's archetype confirmed by ≥2 of 3 neighbors) added then removed | Worked at small corpus sizes but became redundant once we moved to per-archetype scoring | Replaced with **score_gap** (margin over runner-up) — better explainability anyway |

## Final calibration (threshold 0.865)
| | FPR / TPR | Score range |
|---|---|---|
| 100 random normal transactions | 3% flagged | p50 0.84, p99 0.87 |
| `card_testing` (20 injected) | 18/20 (90%) | 0.86–0.89 |
| `foreign_cnp` (20 injected) | 15/20 (75%) | 0.85–0.90 |
| `account_takeover` (20 injected) | 19/20 (95%) | 0.86–0.89 |

Top-archetype identification is **20/20** for all three archetypes — even the misses correctly identify the pattern, they just score below threshold.

## Deviations from the playbook
- **3 archetypes** (`card_testing`, `foreign_cnp`, `account_takeover`) instead of 5. `amount_anomaly` and `merchant_category_fraud` dropped (signal-to-noise).
- **Per-archetype filtered `$vectorSearch`** instead of single global search — picks best match within each archetype rather than top-K globally.
- **Tag-style embedding text with raw values** instead of the playbook's natural-language template — wider cosine spread for the same semantic content.
- **Threshold 0.865** instead of 0.78 — calibrated empirically against 100 random normals.
- **10× volume bump** (per user request): 1,200 fraud examples + 100,000 normal transactions instead of the playbook's 200 + 10,000.
- **Foreign-CNP corpus bug fix** in `data/generate.py`: `random.choice(["NG","RU","BR","AU"])` → `random.choice(["NG","RU","BR"])` so the archetype is genuinely foreign.

## Final layout
```
fraud-detect/
├── app.py                 ← Streamlit dashboard
├── requirements.txt
├── .env / .env.example
├── src/
│   ├── db.py
│   ├── embed.py
│   └── core.py            ← per-archetype $vectorSearch
├── scripts/
│   ├── check_env.py
│   ├── create_db_index.py
│   ├── verify_data.py
│   ├── smoke_test.py
│   ├── calibrate.py
│   ├── probe.py
│   └── _ping.py
└── data/
    └── generate.py
```

## Run book (recap)
```bash
cd fraud-detect && source venv/bin/activate
python -m scripts.check_env         # 5/5 OK
python -m scripts.create_db_index   # idempotent
python -m data.generate             # ~50 s, negligible cost
python -m scripts.verify_data       # 9 checks
python -m scripts.smoke_test        # 3 hand-crafted cases
python -m scripts.calibrate         # FPR + per-archetype TPR
streamlit run app.py                # localhost:8501
```

## Outstanding
- `amount_anomaly` is genuinely hard to detect with vector cosine alone on the current text representation — re-adding it would need either a different signal source (rule layer, feature-match bonus) or a more numerically-aware embedding model.
- ~3% of streaming normal transactions cross the 0.865 threshold (calibrated tail). Visible as the rare red row in the feed even with no inject.
- All other PoCs (`ai-kb/`, `govtech-casemgmt/`, `iot-telemetry/`) remain empty scaffolds with their playbooks ready under `plans/`.
