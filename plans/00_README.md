# MongoDB APAC PoC Implementation Plans

This folder contains five detailed implementation plans, one per customer-segment proof-of-concept. Each plan is self-contained and assumes one engineer working alone for an afternoon (3–4 hours).

## File index

| # | File | Customer Segment | Demonstrates |
|---|------|------------------|--------------|
| 1 | `01_FinServ_Fraud_Detection.md` | Financial Services & Fintech | Real-time anomaly detection via Vector Search; one platform replaces operational DB + vector DB + ML pipeline |
| 2 | `02_DigitalNative_Hybrid_Search.md` | Digital-Native Tech | Lexical + semantic + filter search in one cluster; one platform replaces operational DB + Elasticsearch + Pinecone |
| 3 | `03_AI_RAG_Knowledge_Base.md` | AI-Native / Legacy Modernization | Self-updating RAG with auto-embedding; the synchronization tax disappears |
| 4 | `04_GovTech_Case_Management.md` | Government & Public Sector | Polymorphic citizen data + field-level encryption + unified search |
| 5 | `05_Telco_IoT_Telemetry.md` | Telecommunications | Time-series ingestion at scale + real-time aggregations + anomaly alerting |

## Common prerequisites (all PoCs)

Before starting any implementation, have these in place:

- **MongoDB Atlas account** — sign up at mongodb.com/atlas
- **Cluster sizing** — M0 (free) for PoCs 4 only; M10 (~USD 0.08/hr) recommended for PoCs 1, 2, 3, 5 because of Vector Search performance, time-series scale, or both
- **Python 3.11+** with `pip` and a virtual environment tool
- **Voyage AI API key** — sign up at voyageai.com (PoCs 1, 2, 3); free tier covers all demos in this playbook
- **Anthropic API key** — sign up at console.anthropic.com (PoC 3 only)
- **Code editor** — VS Code or equivalent
- **Git** — optional but recommended for snapshotting progress

## Recommended project layout

For each PoC, use this directory structure:

```
poc-name/
├── .env                    # API keys and connection strings (gitignored)
├── .env.example            # Template with empty values
├── requirements.txt        # Python dependencies
├── data/
│   └── generate.py         # Synthetic data generator
├── src/
│   ├── db.py              # MongoDB connection helpers
│   ├── embed.py           # Voyage AI embedding helpers (where applicable)
│   └── core.py            # PoC-specific business logic
├── app.py                  # Streamlit entry point
└── README.md               # Demo script and notes
```

## Cost expectations

Running any one PoC for a single afternoon and tearing it down the same day costs under USD 5 in cloud spend, including:
- Atlas M10 cluster: ~USD 2 for 4 hours
- Voyage AI embeddings: well under USD 1 for the data volumes used
- Anthropic Claude API: under USD 1 for the chat demo (PoC 3)

If you keep clusters running for multi-week pilot use, expect ~USD 60/month per M10 cluster.

## Cleanup checklist (run after every demo)

1. Pause or terminate the Atlas cluster
2. Revoke the Atlas database user created for the PoC
3. Rotate the Voyage AI / Anthropic API keys if they were shared
4. Archive the project directory; remove `.env` before sharing

## Implementation order recommendation

If building all five sequentially, this order minimizes context-switching:

1. Start with **PoC 2 (Hybrid Search)** — establishes Atlas Search + Vector Search patterns used elsewhere
2. Then **PoC 1 (Fraud Detection)** — reuses vector search patterns from PoC 2
3. Then **PoC 3 (RAG)** — adds the auto-embedding and Claude integration
4. Then **PoC 5 (IoT Telemetry)** — different mode (time-series, async ingestion)
5. Last **PoC 4 (Citizen Services)** — polymorphic data + field-level encryption is its own track

A single engineer building all five end-to-end should plan for ~2 working days, not five afternoons, because of compounding setup time and shared-component reuse.
