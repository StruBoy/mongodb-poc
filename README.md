# MongoDB APAC PoC playbook

Five customer-segment proofs-of-concept built on MongoDB Atlas. Each one demonstrates that a single Atlas cluster can replace a multi-system stack (operational DB + search + vector + warehouse). The detailed playbooks live under [`plans/`](./plans).

| # | PoC | Status | Demonstrates |
|---|---|---|---|
| 1 | [Fraud Detection](./plans/01_FinServ_Fraud_Detection.md) (FinServ) | **Implemented** in [`fraud-detect/`](./fraud-detect) | Vector similarity for live anomaly scoring |
| 2 | [Hybrid Search](./plans/02_DigitalNative_Hybrid_Search.md) (Digital-native) | **Implemented** in [`hybrid-search/`](./hybrid-search) | Atlas Search + Vector Search + `$rankFusion` |
| 3 | [RAG Knowledge Base](./plans/03_AI_RAG_Knowledge_Base.md) (AI-native) | **Implemented** in [`ai-kb/`](./ai-kb) | Self-updating retrieval with no sync pipeline |
| 4 | [Citizen Case Mgmt](./plans/04_GovTech_Case_Management.md) (GovTech) | Not started | Polymorphic docs + field-level encryption |
| 5 | [IoT Telemetry](./plans/05_Telco_IoT_Telemetry.md) (Telco) | **Implemented** in [`iot-telemetry/`](./iot-telemetry) | Time-series collections + real-time aggregations |

## Prerequisites

- MongoDB Atlas cluster (M10 recommended; **8.1+** required for hybrid-search's `$rankFusion`, any 8.x is fine for the others)
- `pocuser` account with `readWriteAnyDatabase` role
- Atlas connection string in each PoC's `.env`, see `.env.example`
- Voyage AI account with payment method on file (PoCs 1, 2, 3 — `voyage-3` embeddings)
- Anthropic API key (PoCs 2, 3 — Claude haiku 4.5 for description synthesis and RAG generation)
- Python 3.11+
- IoT telemetry PoC (5) needs neither Voyage nor Anthropic — pure time-series + aggregations

Each PoC lives in its own directory with its own venv, `.env`, and Atlas project. They can be deployed independently.

## Deploy the Hybrid Search PoC

Total spin-up time on a clean machine: ~15 min, plus ~10 min for catalog generation.

### 1. Provision the Atlas cluster

In the Atlas console:

1. Create project `poc-hybrid-search`
2. Build cluster: **M10**, MongoDB version 8.1 or higher
3. **Database Access** → add user `pocuser` with `readWriteAnyDatabase`
4. **Network Access** → add your current IP (or `0.0.0.0/0` for the demo only — revoke after)
5. **Connect → Drivers** → copy the SRV connection string
6. Confirm the cluster's MongoDB version is **8.1 or higher** (Edit Configuration → Additional Settings → MongoDB Version). Required for the native `$rankFusion` aggregation stage.

### 2. Configure local environment

```bash
cd hybrid-search
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set:
#   MONGODB_URI=<paste from Atlas Connect, with your password substituted>
#   VOYAGE_API_KEY=<from voyageai.com — payment method must be on file>
#   ANTHROPIC_API_KEY=<from console.anthropic.com>
```

### 3. Verify connectivity

```bash
python -m scripts.check_env
```

Runs six checks: env vars, MongoDB connection, database/collection presence, Voyage authentication, Voyage rate-limit (confirms a payment method is configured), and Anthropic authentication. All six should report `[OK]` before proceeding.

### 4. Create the database, collection, and search indexes

```bash
python -m scripts.create_db_index
```

Idempotent. Performs plan sections 1.3 (database + collection) and 1.4 (Atlas Search index `products_text_idx` and Vector Search index `products_vector_idx`), then polls until both indexes report `queryable: true`.

### 5. Generate the catalog

```bash
python -m data.generate
```

Synthesizes ~2,500 products across 4 categories × 5 product types, generates realistic descriptions via Claude (in batches of 25), embeds each product with `voyage-3` (1024-dim, in batches of 50), and inserts. Takes ~10 min and costs ~USD 0.40 (Claude descriptions dominate).

### 6. Verify the catalog loaded correctly

```bash
python -m scripts.verify_catalog
```

Confirms count (~2,500), required fields, embedding dimensionality, category and brand coverage, and description quality. Exits non-zero on any structural failure.

### 7. Smoke-test the three search modes

```bash
python -m scripts.smoke_test
```

Runs keyword (Atlas Search), semantic (Vector Search), and hybrid (`$rankFusion`) against the query `"shoes for running long distances"` with category=footwear, max_price=400. Should return 5 hits per mode with sensible scores.

### 8. Launch the demo UI

```bash
streamlit run app.py
```

Two tabs at `http://localhost:8501`:
- **🔍 Search compare** — three side-by-side panes (keyword / semantic / hybrid) for any query
- **📋 Full catalog** — sortable, filterable table of every product

The sidebar offers three pre-baked demo queries that highlight when each mode wins.

### Demo flow

The story: one Atlas cluster, three retrieval modes, and `$rankFusion` merging them in a native aggregation stage — no separate vector DB, no embedding pipeline, no sync layer.

1. **Marathon training** sidebar query — all three modes return relevant footwear, but the hybrid panel ranks the marathon-relevant products first by combining brand-matched and intent-matched signals.
2. **Brand search** ("TrailMaster") — keyword nails the brand directly; semantic drifts toward thematically similar but unbranded items. Hybrid prioritizes the exact-brand hits.
3. **Long flights** ("comfortable for long flights") — semantic understands intent and surfaces noise-cancelling headphones; keyword has nothing useful to match against. Hybrid passes the semantic ranking through.
4. Switch to the **📋 Full catalog** tab to show the same documents are available for operational queries — same cluster, same collection.

Architectural points to land:
- One cluster, one query language, no sync pipelines.
- `$rankFusion` is a native aggregation stage (MongoDB 8.1+) — reciprocal-rank-fusion without client-side glue.
- The same `products` collection powers keyword, semantic, and hybrid retrieval simultaneously.

## Deploy the Fraud Detection PoC

Total spin-up time on a clean machine: ~10 min, plus ~1 min for corpus generation.

### 1. Provision the Atlas cluster

In the Atlas console:

1. Create project `poc-fraud-detection`
2. Build cluster: **M10**, any MongoDB 8.x
3. **Database Access** → add user `pocuser` with `readWriteAnyDatabase`
4. **Network Access** → add your current IP (or `0.0.0.0/0` for the demo only — revoke after)
5. **Connect → Drivers** → copy the SRV connection string

### 2. Configure local environment

```bash
cd fraud-detect
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set:
#   MONGODB_URI=<paste from Atlas Connect, with your password substituted>
#   VOYAGE_API_KEY=<from voyageai.com — payment method must be on file>
```

No Anthropic key required — fraud examples are synthesized with `faker`, not Claude.

### 3. Verify connectivity

```bash
python -m scripts.check_env
```

Five checks: env vars, MongoDB connection, `fraud_demo` db / collections, Voyage authentication, Voyage billing probe. All five must report `[OK]`.

### 4. Create the database, collections, and vector index

```bash
python -m scripts.create_db_index
```

Idempotent. Creates `fraud_demo.fraud_examples` and `fraud_demo.transactions`, plus the 1024-dim cosine vector index `fraud_vector_idx` (with `archetype` as a filter field), and polls until queryable.

### 5. Generate the corpus

```bash
python -m data.generate
```

Generates 1,200 labeled fraud examples (3 archetypes × 400) and 100,000 normal transactions. Embeds the fraud corpus with `voyage-3` (1024-dim) in batches of 50. Takes ~1 minute. Cost is well under Voyage's free 200M-token allowance.

### 6. Verify the data loaded correctly

```bash
python -m scripts.verify_data
```

Confirms counts (~1,200 fraud / ~100,000 normal), required fields, embedding shape, per-archetype distribution, and label sanity. Exits non-zero on any structural failure.

### 7. Smoke-test the scorer

```bash
python -m scripts.smoke_test
```

Runs three hand-crafted transactions through `score_transaction`: a foreign card-not-present transaction, a benign local groceries purchase, and an off-hours large online purchase. All three should classify correctly under the calibrated threshold.

### 8. (Optional) Calibrate the threshold

```bash
python -m scripts.calibrate
```

Samples 100 random normal transactions plus 20 of each fraud archetype, reports false-positive rate by threshold and per-archetype true-positive rate. Use the output to tune `ANOMALY_THRESHOLD` in `src/core.py` (currently `0.865`, giving ~3% FPR on normals and 75–95% TPR per archetype).

### 9. Launch the demo dashboard

```bash
streamlit run app.py
```

At `http://localhost:8501`:
- **Sidebar** — fraud archetype selector, 💉 Inject button, auto-stream toggle, stream-interval slider, reset, and live session stats.
- **Live transaction feed** — every scored transaction with red/green badge, top archetype, score, gap over runner-up, and end-to-end latency.
- **Anomaly alerts** — expand any alert to see the best match within each archetype, the score margin, the exact text that was embedded for the query, and per-alert latency.

### Demo flow

The story: each incoming transaction is scored against a corpus of known fraud patterns using vector similarity. The matched archetype and the margin over the runner-up explain *why* — no rules engine, alert latency under ~250 ms end to end.

1. **Set the scene (30 sec).** Auto-stream is on by default — normal transactions flow in every 2 seconds, mostly green. Each one is being scored against 1,200 labeled fraud examples via per-archetype filtered `$vectorSearch` in real time.
2. **Inject the demo fraud (30 sec).** With `foreign_cnp` selected, click 💉. Within ~250 ms a red row appears in the feed: risk ~0.88, top archetype `foreign_cnp`, margin around +0.06 over the next-best archetype. A new alert lands in the right pane.
3. **Show explainability (1 min).** Expand the alert:
   - Top-1 match within each of the three archetypes, sorted — `foreign_cnp` clearly wins, runners-up trail by a clean margin
   - Risk score, margin over runner-up, and per-transaction latency
   - The exact tag-style text used to embed the query (popover at the bottom of the alert)
4. **Try the other archetypes.** Switch to `card_testing` or `account_takeover` and inject — each one flags with its own archetype correctly identified.
5. **Land the architectural point (30 sec).** All of this is one Atlas cluster: operational `transactions`, labeled `fraud_examples`, and 1024-dim embeddings live together. No separate vector DB, no rules engine, no sync layer.

Caveats worth acknowledging if asked:
- The PoC ships **3** archetypes (`card_testing`, `foreign_cnp`, `account_takeover`) — the two from the playbook that only differed from normal transactions on the amount axis (`amount_anomaly`, `merchant_category_fraud`) were dropped because voyage-3 cosine doesn't cleanly separate amount-only signals.
- About 3% of normal transactions occasionally cross threshold — visible as the rare red row even with no inject. That's the calibrated tail trade-off; raising the threshold reduces false positives but also reduces archetype sensitivity.

## Deploy the RAG Knowledge Base PoC

Total spin-up time on a clean machine: ~10 min, plus ~2 min for corpus generation.

### 1. Provision the Atlas cluster

In the Atlas console:

1. Create project `poc-rag-knowledge` (or reuse an existing PoC project — each PoC uses its own database, so they coexist on one cluster)
2. Build cluster: **M10**, any MongoDB 8.x
3. **Database Access** → add user `pocuser` with `readWriteAnyDatabase`
4. **Network Access** → add your current IP (or `0.0.0.0/0` for the demo only — revoke after)
5. **Connect → Drivers** → copy the SRV connection string

### 2. Configure local environment

```bash
cd ai-kb
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set:
#   MONGODB_URI=<paste from Atlas Connect, with your password substituted>
#   VOYAGE_API_KEY=<from voyageai.com — payment method must be on file>
#   ANTHROPIC_API_KEY=<from console.anthropic.com>
```

### 3. Verify connectivity

```bash
python -m scripts.check_env
```

Six checks: env vars, MongoDB connection, `kb_demo` db / collection, Voyage authentication, Voyage rate-limit (confirms a payment method is configured), and Anthropic authentication. All six should report `[OK]` before proceeding.

### 4. Create the database, collection, and vector index

```bash
python -m scripts.create_db_index
```

Idempotent. Creates `kb_demo.documents` and the 1024-dim cosine vector index `kb_vector_idx` (with `category` as a filter field), then polls until queryable.

### 5. Generate the corpus

```bash
python -m data.generate
```

Generates 20 enterprise policy documents across 5 categories (HR, IT, Finance, Product, Security), expands each via Claude into a 150–200-word body, and embeds with `voyage-3` on every write through `upsert_document` — the same code path the editor UI uses. Takes ~2 minutes; cost is well under USD 0.10.

### 6. Verify the corpus loaded correctly

```bash
python -m scripts.verify_corpus
```

Confirms count (20), required fields, embedding dimensionality (1024), category coverage, `_id` format, and body length. Exits non-zero on any structural failure.

### 7. Smoke-test the RAG pipeline

```bash
python -m scripts.smoke_test
```

Runs three representative questions ("parental leave", "remote work", "phishing reporting") through `answer_question` and confirms the expected source document is the top hit with non-empty answer text and per-call retrieval/generation timings.

### 8. Launch the demo UI

```bash
streamlit run app.py
```

Split-pane layout at `http://localhost:8501`:
- **Left** — chat with the knowledge base, sources expandable per turn (similarity score, category, retrieval/generation latency)
- **Right** — document editor for any policy; saving re-embeds via `voyage-3` on the same upsert path
- **Sidebar** — five pre-baked demo prompts that exercise every category

### Demo flow

The story: source-of-truth document edits propagate to the AI answer with no sync pipeline. Operational store and vector store are the same store.

1. **Set up the universal RAG problem (1 min).** "Every team building production RAG hits the same wall — source documents update and the embeddings in your vector store go stale. The standard fix is a CDC pipeline, a re-embedding job, and drift monitoring. That's the integration tax in one sentence."
2. **Show the architecture (1 min).** "One MongoDB cluster. Documents and their embeddings live in the same collection. When a document is written, its embedding regenerates as part of the same operation. There is no separate vector store."
3. **Run the demo question (30 sec).** Click the **"How much parental leave do we offer?"** sidebar prompt. The answer cites `hr-001` and mentions twelve weeks of paid leave.
4. **The live-edit moment (2 min).** Switch to the editor pane on the right (defaulted to `hr-001`). In the body, change "twelve weeks" to "16 weeks". Click **💾 Save & re-embed**. The success banner confirms the embedding was just regenerated. Click the parental-leave prompt again — the new answer says 16 weeks.
5. **Land the architectural point (30 sec).** "There is nothing to synchronize because there is nothing separate. That's the platform consolidation argument in one demo."

Architectural points to land:
- One cluster, one query language, no sync pipelines.
- The same `documents` collection holds the source-of-truth body *and* the 1024-dim embedding — `$vectorSearch` reads them together.
- Auto-embedding on write is application-side here for clarity; Atlas Vector Search now also supports native auto-embedding via Voyage AI in preview if you'd rather have the database manage the lifecycle directly.

## Deploy the IoT Telemetry PoC

Total spin-up time on a clean machine: ~10 min, plus ~5 sec for fleet generation. No Voyage / Anthropic keys required — this PoC is pure time-series + aggregations.

### 1. Provision the Atlas cluster

In the Atlas console:

1. Create project `poc-telco-telemetry` (or reuse an existing PoC project — each PoC uses its own database, so they coexist on one cluster)
2. Build cluster: **M10**, any MongoDB 8.x (the cluster region in `ap-southeast-1` matches the simulated fleet, but isn't required)
3. **Database Access** → add user `pocuser` with `readWriteAnyDatabase`
4. **Network Access** → add your current IP (or `0.0.0.0/0` for the demo only — revoke after)
5. **Connect → Drivers** → copy the SRV connection string

### 2. Configure local environment

```bash
cd iot-telemetry
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set:
#   MONGODB_URI=<paste from Atlas Connect, with your password substituted>
```

### 3. Verify connectivity

```bash
python -m scripts.check_env
```

Three checks: env vars, MongoDB ping, and `telco_demo` database / collection reachability. Collections are reported as `[info] not yet created` on first run — that's expected.

### 4. Create the database, time-series collection, and indexes

```bash
python -m scripts.create_db_index
```

Idempotent. Creates `telco_demo.telemetry` as a time-series collection (`timeField=ts`, `metaField=meta`, `granularity=seconds`, `expireAfterSeconds=86400`), the regular `towers` and `control_failures` collections, and two compound indexes on `telemetry` (`{meta.tower_id, ts}` and `{meta.region, ts}`).

Time-series collections cannot have their options modified after creation; on re-run the script detects the existing collection and either skips (if options match) or fails loudly (if drift). Drop manually and re-run if you really mean to recreate.

### 5. Generate the tower fleet

```bash
python -m data.generate_fleet
```

Inserts 1,000 cell towers across three Singapore districts: **Central** (CBD/Marina Bay/Orchard, 350 towers), **East** (Tampines/Changi/Bedok, 300), and **West** (Jurong/Clementi/Bukit Timah, 350). Tower IDs are district-prefixed (`TWR-CEN-0001` … `TWR-WST-0350`) so the audience can read them directly in the dashboard. Takes a few seconds; no API costs.

### 6. Verify the fleet loaded correctly

```bash
python -m scripts.verify_data
```

Eight checks: tower count, district distribution, ID prefix matches region, required fields, tower types, time-series collection options, telemetry document shape, and telemetry recency. The last two warn rather than fail if you haven't started the streamer yet.

### 7. Start the telemetry stream (separate terminal)

```bash
python -m data.stream_telemetry
```

Async motor-based generator. Pumps ~1,000 events/sec across the fleet (1 event per tower per cycle, with parallel batched `insert_many` in chunks of 500). Each cycle reads `telco_demo.control_failures` to know which towers should emit degraded metrics. Leave running; Ctrl+C stops cleanly.

For higher throughput pass `--rate 2000` etc. — at ~5,000 events/sec on M10 you'll start seeing connection pool pressure; M30 handles 10,000+.

### 8. Smoke-test the end-to-end pipeline

In your original terminal (with the stream still running in the other):

```bash
python -m scripts.smoke_test
```

Seven steps: confirms the stream is alive, runs all four analytics queries against live data, injects 3 failures into `West`, waits 8 seconds, confirms those 3 towers cross the 5%-packet-loss threshold, and cleans up the control rows so the dashboard opens green.

### 9. Launch the dashboard

```bash
streamlit run app.py
```

At `http://localhost:8501`:

- **Top metrics:** live write rate, towers reporting, district count, active failures
- **District health (last 30 sec):** one card per Singapore district with avg signal / packet loss / throughput / temperature, red badge above 3% packet loss
- **Throughput timeline:** per-district stacked-line Plotly chart over the last 2 minutes
- **Tower fleet map:** all 1,000 towers plotted on a Singapore map, recoloured red as failures are injected
- **Towers needing attention:** expandable list of towers above the 5% packet-loss threshold
- **Sidebar:** per-district inject buttons (`Central` / `East` / `West`, 3 towers each), a `🔥 Major incident` button (15 towers in West), `✅ Clear all failures`, and a refresh-interval slider

### Demo flow

The story: one Atlas cluster ingests telemetry at ~1,000 events/sec, stores it in a time-series collection, and runs the dashboard's aggregations directly off the same collection. No streaming tier, no warehouse, no sync layer.

1. **Frame the problem (1 min).** "If you run technology in an APAC telco, network telemetry is your hardest data architecture problem. The standard answer is Kafka feeding a time-series database feeding a warehouse. Three systems minimum, often more, with all the operational overhead and ETL fragility that implies."
2. **Show the architecture (1 min).** "This is one MongoDB cluster. The `telemetry` collection is a time-series collection — purpose-built for this shape of data. The same cluster ingests, stores, queries, and feeds the dashboard you're looking at."
3. **Steady state (30 sec).** Point at the live write-rate counter (~1,000 ops/sec). All three districts green, throughput chart steady, the tower map is a sea of green dots over Singapore.
4. **Inject the failure (1.5 min).** Click `West` in the sidebar. Within ~5 seconds three towers in Jurong/Clementi/Bukit Timah turn red on the map, the West throughput line dips, the West district card flips to red, and three towers appear in the "needs attention" list with their specific signal / packet-loss / temperature numbers.
5. **Major incident (optional).** Click `🔥 Major incident: 15 towers in West`. The dashboard fills with red — same query path, just more rows back from the same `$group` aggregation.
6. **Land the architectural point (30 sec).** "Every system you remove from the architecture is one fewer point of failure during a major incident — and major incidents are when you find out which architectural shortcuts you took five years ago."

Architectural points to land:
- One cluster, time-series + regular collections side by side, native real-time aggregations.
- Failure-injection state lives in `control_failures` — same database that holds the telemetry. Operational metadata and time-series telemetry coexist in one place.
- The 24-hour TTL (`expireAfterSeconds: 86400`) is a one-line collection option, not a separate retention service.

## Project layout

```
mongodb_poc/
├── README.md              ← this file
├── plans/                 ← playbook docs for all five PoCs
├── fraud-detect/          ← implemented PoC #1
│   ├── app.py             ← Streamlit dashboard
│   ├── requirements.txt
│   ├── .env.example
│   ├── src/               ← db, embed, core (per-archetype filtered $vectorSearch)
│   ├── scripts/           ← check_env, create_db_index, verify_data, smoke_test, calibrate, probe
│   └── data/              ← generate.py
├── hybrid-search/         ← implemented PoC #2
│   ├── app.py             ← Streamlit UI
│   ├── requirements.txt
│   ├── .env.example
│   ├── src/               ← importable modules: db, embed, core
│   ├── scripts/           ← CLI utilities run via `python -m scripts.<name>`
│   └── data/              ← generate.py
├── ai-kb/                 ← implemented PoC #3
│   ├── app.py             ← Streamlit chat + editor (split-pane)
│   ├── requirements.txt
│   ├── .env.example
│   ├── src/               ← db, embed, docs (auto-embed on upsert), rag
│   ├── scripts/           ← check_env, create_db_index, verify_corpus, smoke_test
│   └── data/              ← generate.py
├── govtech-casemgmt/      ← placeholder for PoC #4
└── iot-telemetry/         ← implemented PoC #5
    ├── app.py             ← Streamlit operations dashboard
    ├── requirements.txt
    ├── .env.example
    ├── src/               ← db (sync + async), analytics
    ├── scripts/           ← check_env, create_db_index, verify_data, smoke_test
    └── data/              ← generate_fleet.py, stream_telemetry.py
```

## Cleanup

After the demo:

1. Pause or terminate the Atlas cluster(s) — each PoC has its own project
2. Revoke the `pocuser` database user
3. Remove `0.0.0.0/0` from Network Access if you opened it
4. (Optional) `deactivate` and `rm -rf <poc-dir>/venv`

Running an M10 idle costs ~USD 60/month — pause it if you're not actively demoing.
