# MongoDB APAC PoC playbook

Five customer-segment proofs-of-concept built on MongoDB Atlas. Each one demonstrates that a single Atlas cluster can replace a multi-system stack (operational DB + search + vector + warehouse). The detailed playbooks live under [`plans/`](./plans).

| # | PoC | Status | Demonstrates |
|---|---|---|---|
| 1 | [Fraud Detection](./plans/01_FinServ_Fraud_Detection.md) (FinServ) | Not started | Vector similarity for live anomaly scoring |
| 2 | [Hybrid Search](./plans/02_DigitalNative_Hybrid_Search.md) (Digital-native) | **Implemented** in [`hybrid-search/`](./hybrid-search) | Atlas Search + Vector Search + `$rankFusion` |
| 3 | [RAG Knowledge Base](./plans/03_AI_RAG_Knowledge_Base.md) (AI-native) | Not started | Self-updating retrieval with no sync pipeline |
| 4 | [Citizen Case Mgmt](./plans/04_GovTech_Case_Management.md) (GovTech) | Not started | Polymorphic docs + field-level encryption |
| 5 | [IoT Telemetry](./plans/05_Telco_IoT_Telemetry.md) (Telco) | Not started | Time-series collections + real-time aggregations |

## Prerequisites
- MongoDB Atlas Cluster M10 (8.1+ for `$rankFusion`)
- `pocuser` account with `readWriteAnyDatabase` role
- Atlas connection string in `.env`, see `.env.example`
- Voyage AI account with payment method configured
- Anthropic API key
- Python 3.11+

## Deploy the Hybrid Search PoC

The only PoC currently implemented end-to-end. Total spin-up time on a clean machine: ~15 min, plus ~10 min for catalog generation.

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
python -m venv venv
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

## Project layout

```
mongodb_poc/
├── README.md              ← this file
├── plans/                 ← playbook docs for all five PoCs
├── hybrid-search/         ← implemented PoC #2
│   ├── app.py             ← Streamlit UI
│   ├── requirements.txt
│   ├── .env.example
│   ├── src/               ← importable modules: db, embed, core
│   ├── scripts/           ← CLI utilities run via `python -m scripts.<name>`
│   └── data/              ← generate.py
├── fraud-detect/          ← placeholder for PoC #1
├── ai-kb/                 ← placeholder for PoC #3
├── govtech-casemgmt/      ← placeholder for PoC #4
└── iot-telemetry/         ← placeholder for PoC #5
```

## Cleanup

After the demo:

1. Pause or terminate the Atlas cluster
2. Revoke the `pocuser` database user
3. Remove `0.0.0.0/0` from Network Access if you opened it
4. (Optional) `deactivate` and `rm -rf hybrid-search/venv`

Running an M10 idle costs ~USD 60/month — pause it if you're not actively demoing.
