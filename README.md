# MongoDB APAC PoC playbook

Six customer-segment proofs-of-concept built on MongoDB Atlas. Each one demonstrates that a single Atlas cluster can replace a multi-system stack (operational DB + search + vector + warehouse). The detailed playbooks live under [`plans/`](./plans).

| # | PoC | Status | Demonstrates |
|---|---|---|---|
| 1 | [Fraud Detection](./plans/01_FinServ_Fraud_Detection.md) (FinServ) | **Implemented** in [`fraud-detect/`](./fraud-detect) | Vector similarity for live anomaly scoring |
| 2 | [Hybrid Search](./plans/02_DigitalNative_Hybrid_Search.md) (Digital-native) | **Implemented** in [`hybrid-search/`](./hybrid-search) | Atlas Search + Vector Search + `$rankFusion` |
| 3 | [RAG Knowledge Base](./plans/03_AI_RAG_Knowledge_Base.md) (AI-native) | **Implemented** in [`ai-kb/`](./ai-kb) | Self-updating retrieval with no sync pipeline |
| 4 | [Citizen Case Mgmt](./plans/04_GovTech_Case_Management.md) (GovTech) | **Implemented** in [`govtech-casemgmt/`](./govtech-casemgmt) | Polymorphic docs + field-level encryption |
| 5 | [IoT Telemetry](./plans/05_Telco_IoT_Telemetry.md) (Telco) | **Implemented** in [`iot-telemetry/`](./iot-telemetry) | Time-series collections + real-time aggregations |
| 6 | EMR RAG (Healthcare) | **Implemented** in [`emr-rag/`](./emr-rag) | RAG over patient records + polymorphic visit schemas + PHI encryption |

## Prerequisites

- MongoDB Atlas cluster (M10 recommended; **8.1+** required for hybrid-search's `$rankFusion`, any 8.x is fine for the others)
- `pocuser` account with `readWriteAnyDatabase` role
- Atlas connection string in each PoC's `.env`, see `.env.example`
- Voyage AI account with payment method on file (PoCs 1, 2, 3, 6 — `voyage-3` embeddings)
- Anthropic API key (PoCs 2, 3, 6 — Claude haiku 4.5 for description synthesis and RAG generation)
- Python 3.11+
- IoT telemetry PoC (5) needs neither Voyage nor Anthropic — pure time-series + aggregations
- GovTech case management PoC (4) needs neither Voyage nor Anthropic either — it uses application-side AES-256-GCM for field-level encryption (an extra `ENCRYPTION_KEY` env var, generated locally)
- EMR RAG PoC (6) needs Voyage + Anthropic + an `ENCRYPTION_KEY` (PHI fields use the same AES-256-GCM pattern as PoC 4)

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

Runs keyword, semantic, and hybrid against all three sidebar demo queries and asserts the differentiation, not just non-empty results: keyword should miss on the marathon query, semantic should miss on the brand query, both should contribute on the long-flights query, and hybrid should always come back non-empty. Exits non-zero if any of those expectations break.

### 8. Launch the demo UI

```bash
streamlit run app.py
```

Two tabs at `http://localhost:8501`:
- **🔍 Search compare** — three side-by-side panes (keyword / semantic / hybrid) for any query
- **📋 Full catalog** — sortable, filterable table of every product

The sidebar offers three pre-baked demo queries that highlight when each mode wins.

### Demo flow

The story: one Atlas cluster, three retrieval modes, and `$rankFusion` merging them in a native aggregation stage — no separate vector DB, no embedding pipeline, no sync layer. Each demo query is engineered to make exactly one mode win, so the hybrid pane is the only one that wins all three.

1. **Marathon racing** sidebar query — `"racing 26.2 miles"`. The keyword pane is empty or shows random non-running footwear: footwear descriptions are generated under a banned-words rule that strips `marathon`, `race`, `mile`, `endurance`, `long-distance`, etc., so keyword search has no lexical anchor. The semantic pane surfaces marathon racing and trail running shoes via concept embedding. Hybrid carries the semantic ranking through.
2. **Brand search** ("TrailMaster") — the keyword pane is 100% TrailMaster products (Atlas Search exact-matches the brand field). The semantic pane drifts to other brands' trail-themed shoes because `product_to_text` embeds only `description + category` — the brand never enters the vector. Hybrid leans on the keyword pipeline and prioritises the exact-brand hits.
3. **Long flights** (`"comfortable for long flights"`, electronics) — both modes contribute. Electronics descriptions are unconstrained, so keyword catches `comfortable`, `long`, `flights`, `travel`. Semantic catches the noise-cancelling-headphones intent. Hybrid blends them.
4. Switch to the **📋 Full catalog** tab to show the same documents are available for operational queries — same cluster, same collection.

Architectural points to land:
- One cluster, one query language, no sync pipelines.
- `$rankFusion` is a native aggregation stage (MongoDB 8.1+) — reciprocal-rank-fusion without client-side glue.
- The same `products` collection powers keyword, semantic, and hybrid retrieval simultaneously.

Trade-offs worth flagging if asked:
- Footwear descriptions are deliberately scrubbed of intent vocabulary so the keyword-fail demo lands on a small corpus. In a real catalog you would *want* those words present — semantic and keyword would still differ in ranking, just less dramatically.
- `product_to_text` excludes the brand from the embedded text. In production you would normally include it so semantic also resolves brand-affiliated queries; the demo strips it to make the keyword-wins case unambiguous.

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

## Deploy the GovTech Case Management PoC

Total spin-up time on a clean machine: ~10 min, plus ~20 sec for data generation. No Voyage / Anthropic keys required — the encryption is application-side AES-256-GCM.

### 1. Provision the Atlas cluster

In the Atlas console:

1. Create project `poc-citizen-services` (or reuse an existing PoC project — each PoC uses its own database, so they coexist on one cluster)
2. Build cluster: **M0 (free) is sufficient**, any MongoDB 8.x. M10 also works if you're sharing the cluster with other PoCs.
3. **Database Access** → add user `pocuser` with `readWriteAnyDatabase`
4. **Network Access** → add your current IP (or `0.0.0.0/0` for the demo only — revoke after)
5. **Connect → Drivers** → copy the SRV connection string

### 2. Configure local environment

```bash
cd govtech-casemgmt
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set:
#   MONGODB_URI=<paste from Atlas Connect, with your password substituted>
#   ENCRYPTION_KEY=<generate via the command in .env.example>
```

Generate a fresh AES-256 key:

```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

### 3. Verify connectivity

```bash
python -m scripts.check_env
```

Four checks: env vars, MongoDB ping, `citizen_demo` db / collection presence, and an AES-256-GCM round-trip using the key in `.env`. Collections show as `[info] not yet created` on first run — that's expected.

### 4. Create the database, collections, and Atlas Search index

```bash
python -m scripts.create_db_index
```

Idempotent. Performs plan sections 1.3 (`citizen_demo.cases`, `citizen_demo.citizens`) and 1.4 (the `cases_search_idx` Atlas Search index — **dynamic mapping**, which is the technical detail behind the polymorphism story), then polls until the index reports `queryable: true`.

### 5. Generate the dataset

```bash
python -m data.generate
```

Generates 5,000 citizens and 20,000 cases distributed across 5 wildly different schemas: business_permit, building_permit, complaint, benefit_application, marriage_registration. The first citizen in the generated set is the **demo citizen** — the script plants one of every case type for them so the citizen-view timeline lights up all five emojis. PII fields (`national_id`, `dob`, `tax_file_number`, `partner_national_id`) are encrypted before insert via AES-256-GCM with per-record nonces. Takes ~20 seconds.

The script prints the demo citizen's name and `_id` at the end — the Streamlit UI auto-detects them so you don't need to remember.

### 6. Verify the data loaded correctly

```bash
python -m scripts.verify_data
```

Eight checks: citizen count (~5,000), case count (~20,000), all 5 case types present and balanced, required fields per schema, citizen PII envelopes round-trip, benefit `tax_file_number` envelopes round-trip, sampled `citizen_id` references resolve, Atlas Search index queryable. Exits non-zero on any structural failure.

### 7. Smoke-test the end-to-end pipeline

```bash
python -m scripts.smoke_test
```

Five steps: confirms the demo citizen has all 5 case types in one timeline query; runs cross-case Atlas Search for `noise` (should return complaints) and `cafe` (should return business_permits); inserts a brand-new `ev_charging_station_permit` case type via `add_new_case_type`; confirms it becomes searchable through the dynamic search index within ~2 seconds. Cleans up the demo insert.

### 8. Launch the demo UI

```bash
streamlit run app.py
```

Three views, switched from the sidebar at `http://localhost:8501`:

- **Citizen** — pick a citizen; see their full case history as a timeline. The demo citizen at the top of the dropdown has all five case types planted. The citizen record up top is decrypted on read.
- **Government Officer** — five live metric tiles (one per case type), cross-case search box (`noise`, `cafe`, `extension`, `disability`, `Sydney`), and the **Add a new case type** form pre-loaded with an EV charging station permit example.
- **Encryption Inspector** — raw documents pulled straight from MongoDB without going through `decrypt_pii`. Shows the `_encrypted` envelopes (base64 ciphertext + per-record nonce) for `national_id`, `dob`, `tax_file_number`, and `partner_national_id`.

### Demo flow

The story: every other government IT environment has citizen data fragmented across one system per service. Here, every case type lives in one MongoDB collection, queryable together — and adding a new service is an `insertOne`, not a project plan.

1. **Frame the structural problem (1 min).** "In every government environment we walk into, citizen data lives across dozens of systems — one for permits, one for benefits, one for complaints. Each has its own database, its own schema, its own deployment cycle. When a citizen calls and asks 'what's the status of all my interactions with the council', there is no answer because there is no unified view."
2. **The citizen view (2 min).** Switch to **Citizen** mode. The demo citizen is at the top of the dropdown. "This is one MongoDB collection. Five completely different case types — a marriage registration, a business permit, a noise complaint, a disability benefit, a building permit — each with a totally different shape. The marriage record has a celebrant ID. The benefit application has household income. The complaint has a severity field. They all live together. They are all queryable together."
3. **The officer search (1 min).** Switch to **Government Officer** mode. "Search 'noise' — we get complaints. Search 'cafe' — we get business permits. Search 'extension' — we get building permits. One search index, every case type. The dynamic mapping indexes every field that appears in any document, regardless of which schema it came from."
4. **The migration moment (1 min).** Scroll down to the **Add a new case type** form. "The city council just announced a new permit category — EV charging stations. In every other system you've worked with, this would be a project. Schema change, migration window, regression testing, deployment. Here it's this." Click **Insert new case type**. "It's there. It's searchable. The citizen view will display it correctly. Nothing was migrated."
5. **The encryption point (30 sec).** Switch to **Encryption Inspector** mode. "And before anyone in your security team asks: this is what the data actually looks like in MongoDB. National IDs, dates of birth, and tax file numbers are encrypted at the field level. Only authorized application code holding the key can decrypt them. The auditor's first question — how is sensitive PII protected — has a clear answer. In production, MongoDB Queryable Encryption pushes this down into the database itself; the architectural pattern is the same."
6. **Land the architectural point (30 sec).** "This isn't about MongoDB being clever. It's about the document model removing a constraint that forces every other system to fragment citizen data across silos. When the schema can vary per record, you stop needing a separate system per service."

Architectural points to land:
- One cluster, one collection, one query language — five completely different case-type schemas inside it.
- `dynamic: true` on the Atlas Search index turns the polymorphism advantage into a unified search story without per-type mappings.
- Field-level encryption for PII is application-side AES-256-GCM here for clarity; in production, MongoDB Queryable Encryption pushes the same pattern into the database itself with key-vault-managed keys and equality / range query support against ciphertext.

## Deploy the EMR RAG PoC

Total spin-up time on a clean machine: ~10 min, plus ~1 min for seed generation. Combines the auto-embedding RAG pattern from PoC #3 with the polymorphic-collection + field-encryption pattern from PoC #4. The demo's clinical arc — undiagnosed obstructive sleep apnoea unifying treatment-resistant anxiety, prediabetes, and nocturnal arrhythmia — is grounded in real clinical literature so the prognosis shift in the AI summary is plausible to clinically-literate audiences.

### 1. Provision the Atlas cluster

In the Atlas console:

1. Create project `poc-emr-rag` (or reuse an existing PoC project — each PoC uses its own database, so they coexist on one cluster)
2. Build cluster: **M10**, any MongoDB 8.x
3. **Database Access** → add user `pocuser` with `readWriteAnyDatabase`
4. **Network Access** → add your current IP (or `0.0.0.0/0` for the demo only — revoke after)
5. **Connect → Drivers** → copy the SRV connection string

### 2. Configure local environment

```bash
cd emr-rag
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set:
#   MONGODB_URI=<paste from Atlas Connect, with your password substituted>
#   VOYAGE_API_KEY=<from voyageai.com — payment method must be on file>
#   ANTHROPIC_API_KEY=<from console.anthropic.com>
#   ENCRYPTION_KEY=<generate via the command in .env.example>
```

Generate a fresh AES-256 key:

```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

### 3. Verify connectivity

```bash
python -m scripts.check_env
```

Seven checks: env vars, MongoDB ping, `emr_demo` db / collections, AES-256-GCM round-trip on the encryption key, Voyage authentication, Voyage rate-limit (confirms a payment method is configured), and Anthropic authentication. All seven should report `[OK]`.

### 4. Create the database, collections, and vector index

```bash
python -m scripts.create_db_index
```

Idempotent. Creates `emr_demo.patients` and `emr_demo.visits`, plus the 1024-dim cosine vector index `kb_visits_idx` (with `patient_id` and `specialty` as filter fields), then polls until queryable.

### 5. Generate the seed patient and visits

```bash
python -m data.generate
```

Creates one demo patient (`pat-001`, "Maria Chen", 47F, BMI 31) with three encrypted PHI fields (national_id, dob, insurance_id) and **6 seed visits** across 4 specialties — general practice, psychiatry, cardiology, endocrinology — drawn over the last 8 months. Each visit's body is expanded by Claude haiku 4.5 from a structured fixture (specialty, vitals, scores, lab values, plan) and then embedded via voyage-3 on the same write path the editor uses. Takes ~1 minute; cost is well under USD 0.10. The seed picture is intentionally fragmented — nothing in the seed mentions OSA, sleep apnoea, AHI, or CPAP. That's what makes the diagnostic pivot in step 8 land.

### 6. Verify the seed loaded correctly

```bash
python -m scripts.verify_data
```

Seven checks: demo patient exists with PHI envelopes that round-trip, exactly 6 seed visits, all required visit fields present, embeddings are 1024-dim, all 4 expected specialties represented, no body shorter than 400 chars (catches fallback templates), no premature OSA terms in the seed, vector index queryable. Exits non-zero on any structural failure.

### 7. Smoke-test the prognosis arc

```bash
python -m scripts.smoke_test
```

Six steps that prove the demo's "wow" lands automatically:
1. Generate clinical summary BEFORE follow-ups; assert no confident OSA diagnosis (no AHI / polysomnography / CPAP).
2. Ask "What's driving her poor sleep?" → assert top source is a psychiatry visit.
3. `add_followup_batch` → asserts 4 new visits land, including 1 `sleep_medicine` (the novel specialty).
4. Re-summarise; assert the new summary explicitly diagnoses OSA, cites diagnostic evidence, and frames OSA as the unifying upstream cause. Prints both summaries side-by-side.
5. Ask "What is her CPAP plan?" → assert sleep_medicine is the top source (proves the novel-specialty visit was indexed end-to-end with no migration step).
6. Re-ask the sleep question → assert the sleep_medicine visit now appears among sources (same question, fundamentally better answer).
Cleanup removes the follow-up batch so the demo opens clean.

### 8. Launch the demo UI

```bash
streamlit run app.py
```

At `http://localhost:8501`:

- **Sidebar** — persona toggle (`🩺 GP view` / `🧑 Patient view`), patient selector, **➕ Add follow-up visits** and **↺ Reset to seed** buttons, persona-specific demo prompts, clear-chat
- **Patient header** — name, age, BMI, primary GP, plus a collapsible PHI panel that decrypts national_id / dob / insurance_id from their AES-256-GCM envelopes
- **📋 AI overview** — 3–5 sentence summary plus 3 focus-area bullets, generated on demand. Persona toggle changes the voice (clinical vs plain English) without changing the data
- **🗂️ Visit timeline** — chronological, expandable per visit, color-coded by specialty (each card shows the full body and the structured fields)
- **💬 Q&A chat** — patient-scoped `$vectorSearch` filtered by `patient_id`; sources expander shows visit ID, specialty, date, and similarity score per source

### Demo flow

The story: every other healthcare data architecture has visits fragmented across one system per specialty. Here, every specialty's notes — with completely different shapes — live in one MongoDB collection, every insert auto-embeds, the AI summary is always reading current data, and adding a never-before-seen specialty is an `insertOne`, not a project plan.

1. **Frame the structural problem (1 min).** "Patient records live across specialty silos — each clinic system has its own schema, its own database, its own search. The cost is not just integration: it's that nobody sees the picture across specialties, and patients like the one I'm about to show you slip through the gaps for months."
2. **GP view, steady state (1.5 min).** Open Maria Chen's record in 🩺 GP view. The AI overview summarises 6 visits across 4 specialties: prediabetes worsening despite metformin, treatment-resistant anxiety + insomnia, palpitations attributed to anxiety. Read the prognosis aloud — the trajectory isn't converging. Scroll the timeline; point at the cardiology ECG fields vs the psych PHQ-9 vs the endo HbA1c. **All different shapes, one MongoDB collection.**
3. **Patient view (45 sec).** Toggle to 🧑 Patient view, click 🔄 Refresh summary. Same data, plain English, gentle non-specific focus areas because there's no clear answer yet.
4. **The diagnostic pivot — *the moment* (2 min).** Back in GP view, click ➕ Add follow-up visits. The summary auto-refreshes. Read the new prognosis aloud — newly diagnosed moderate-severe OSA (AHI 24) **retrospectively unifying** the prediabetes, the anxiety, and the nocturnal arrhythmia, with a favourable prognosis on CPAP. Three points to land while the audience reads:
   - The summary's *prognosis* changed from "fragmented, not converging" to "unified, treatable, expect measurable improvement." That's the LLM doing what a good clinician does: integrating across specialties.
   - The new visit type (`sleep_medicine`) has fields no other visit had — `ahi_per_hour`, `nadir_spo2_pct`, `cpap_pressure_cmH2O`. **No schema migration ran.** The collection just accepted the new shape.
   - **No re-embedding pipeline ran.** The four new visits embedded on the same write path as every other insert. The summary you just read came out of a `$vectorSearch` over a corpus that is four documents bigger than it was 30 seconds ago.
5. **Toggle to patient view (45 sec).** Click 🔄 Refresh summary. The plain-English version now leads with the OSA diagnosis and concrete next steps ("Use CPAP every night, aim for 4+ hours"). Same underlying data, different audience, same one MongoDB collection.
6. **Q&A drill-down (60 sec).** Back in GP view, ask "What's driving Maria's poor sleep?" — the answer now cites the sleep_medicine visit. Then "Should we have caught this earlier?" — the LLM looks across the timeline and points at the unrefreshing-sleep mention in the seed psych note, the BMI 31 from the GP visit, and the nocturnal pattern of PVCs. **This is the cross-specialty reasoning that the silos prevent.**
7. **Land the points (30 sec).** "One MongoDB collection holds five specialties with five different shapes. Every insert re-embeds on the same write path so the AI summary is always reading current data. PHI is encrypted at rest in the same documents — flip open the PHI panel to show the ciphertext envelopes. This is one cluster doing what would normally take a clinical data warehouse, a vector store, a re-embedding pipeline, and a separate encryption tier — and the clinical value is what the audience just watched: a treatment-resistant patient whose prognosis flipped because the data was finally in one place."

Architectural points to land:
- One cluster, one `visits` collection, five specialty schemas inside it (the sleep_medicine visit has fields no other visit has).
- Every visit insert re-embeds via voyage-3 on the same write path — `src/visits.py:upsert_visit` is the only code path the seed generator and the add-follow-up button both call, so the AI summary is always reading current data with no separate sync layer.
- PHI fields are application-side AES-256-GCM here for clarity; in production, MongoDB Queryable Encryption pushes the same pattern into the database itself.
- The clinical narrative — OSA unifying treatment-resistant anxiety, metformin-resistant prediabetes, and nocturnal arrhythmia — is well-documented in the literature ("Syndrome Z"), so the prognosis shift the audience watches is grounded in real clinical reasoning.

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
├── govtech-casemgmt/      ← implemented PoC #4
│   ├── app.py             ← Streamlit (Citizen / Officer / Inspector views)
│   ├── requirements.txt
│   ├── .env.example
│   ├── src/               ← db, crypto (AES-256-GCM), services (Atlas Search + add-new-type)
│   ├── scripts/           ← check_env, create_db_index, verify_data, smoke_test
│   └── data/              ← generate.py
├── iot-telemetry/         ← implemented PoC #5
│   ├── app.py             ← Streamlit operations dashboard
│   ├── requirements.txt
│   ├── .env.example
│   ├── src/               ← db (sync + async), analytics
│   ├── scripts/           ← check_env, create_db_index, verify_data, smoke_test
│   └── data/              ← generate_fleet.py, stream_telemetry.py
└── emr-rag/               ← implemented PoC #6
    ├── app.py             ← Streamlit (GP / Patient persona toggle, summary, timeline, chat)
    ├── requirements.txt
    ├── .env.example
    ├── src/               ← db, embed, crypto (AES-256-GCM), patients, visits (auto-embed), rag (persona-aware)
    ├── scripts/           ← check_env, create_db_index, verify_data, smoke_test
    └── data/              ← generate.py (seed via Claude), followup_visits.py (canned demo batch)
```

## Cleanup

After the demo:

1. Pause or terminate the Atlas cluster(s) — each PoC has its own project
2. Revoke the `pocuser` database user
3. Remove `0.0.0.0/0` from Network Access if you opened it
4. (Optional) `deactivate` and `rm -rf <poc-dir>/venv`

Running an M10 idle costs ~USD 60/month — pause it if you're not actively demoing.
