# PoC 1: Real-Time Transaction Anomaly Detection — Implementation Plan

**Customer Segment:** Financial Services & Fintech
**Time Budget:** 3.5 hours
**Cluster Tier:** Atlas M10 (recommended)
**Total Cost (afternoon):** ~USD 3

---

## What you're building

A live transaction stream feeding into a MongoDB Atlas cluster, where each incoming transaction is scored against a corpus of known fraud patterns using vector similarity search. A Streamlit dashboard visualizes the live feed and surfaces anomalies in real time.

## What it demonstrates

- **One platform**: operational data + vector retrieval in the same cluster
- **No rule maintenance**: anomaly detection adapts based on similarity to labeled patterns
- **Explainability**: each alert shows the matched fraud archetypes
- **Speed**: end-to-end alert latency under 200ms

## The demo moment

A foreign card-not-present transaction at an unusual merchant, in the middle of the night, lights up red on the dashboard within milliseconds — alongside the three nearest known-fraud examples that triggered the match. No rules were updated.

---

## Prerequisites

- Atlas account with cluster creation permissions
- Voyage AI API key (`voyage-3` model used here)
- Python 3.11+ with `pip`
- Terminal access

---

## Phase 1: Atlas Setup (30 min)

### 1.1 Provision the cluster

In the Atlas console:

1. Create a new project called `poc-fraud-detection`
2. Build a cluster: M10 tier, AWS, Singapore (`ap-southeast-1`) or your closest APAC region
3. Wait for provisioning (~7 minutes)

### 1.2 Configure access

1. **Database Access** → Add Database User: `pocuser` with a strong password, role `readWriteAnyDatabase`
2. **Network Access** → Add IP Address: your current IP (or `0.0.0.0/0` for the demo only — **revoke after**)
3. Copy the connection string from the cluster's "Connect" button

### 1.3 Create the database and collections

Connect via `mongosh` or Compass and create:

```javascript
use fraud_demo

db.createCollection("transactions")
db.createCollection("fraud_examples")
```

### 1.4 Define the vector search index

In Atlas UI: **Atlas Search** → **Create Index** → **JSON Editor** on `fraud_demo.fraud_examples`:

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
      "path": "archetype"
    }
  ]
}
```

Name the index `fraud_vector_idx`. It will take ~1 minute to build after data is loaded.

---

## Phase 2: Project Setup & Data Generation (45 min)

### 2.1 Project scaffolding

```bash
mkdir poc-fraud-detection && cd poc-fraud-detection
python -m venv venv && source venv/bin/activate
pip install pymongo voyageai faker streamlit python-dotenv pandas
```

Create `.env`:

```
MONGODB_URI=mongodb+srv://pocuser:<password>@<cluster>.mongodb.net/
VOYAGE_API_KEY=pa-xxxxxxxxxxxxxxx
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
    return get_client()["fraud_demo"]
```

### 2.3 Embedding helper (`src/embed.py`)

```python
import os
import voyageai
from dotenv import load_dotenv

load_dotenv()
vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])

def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Embed a batch of texts using Voyage AI's voyage-3 model."""
    result = vo.embed(texts, model="voyage-3", input_type=input_type)
    return result.embeddings

def transaction_to_text(tx: dict) -> str:
    """Convert a transaction document to a contextual string for embedding."""
    return (
        f"Amount {tx['amount']:.2f} {tx['currency']} "
        f"at {tx['merchant_name']} ({tx['merchant_category']}) "
        f"in {tx['country']} via {tx['channel']} "
        f"at {tx['hour_of_day']:02d}:00 local time. "
        f"Card present: {tx['card_present']}. "
        f"Distance from home: {tx['distance_from_home_km']}km."
    )
```

### 2.4 Synthetic data generator (`data/generate.py`)

```python
import random
from datetime import datetime, timedelta
from faker import Faker
from src.db import get_db
from src.embed import embed_texts, transaction_to_text

fake = Faker()
random.seed(42)

MERCHANT_CATEGORIES = [
    "groceries", "fuel", "restaurant", "online_retail",
    "electronics", "travel", "entertainment", "atm_withdrawal",
    "utilities", "subscription"
]

# Five fraud archetypes with descriptive prototypes
FRAUD_ARCHETYPES = {
    "card_testing": {
        "description": "Small-amount transactions in rapid succession to test stolen card validity",
        "amount_range": (1, 10),
        "channel": "online",
        "card_present": False,
        "examples": 40
    },
    "foreign_cnp": {
        "description": "Card-not-present transaction from a country far from cardholder home",
        "amount_range": (100, 800),
        "channel": "online",
        "card_present": False,
        "examples": 40
    },
    "account_takeover": {
        "description": "Large purchase at unusual merchant category at unusual hour",
        "amount_range": (500, 3000),
        "channel": "online",
        "card_present": False,
        "examples": 40
    },
    "amount_anomaly": {
        "description": "Single transaction far exceeding typical spending pattern",
        "amount_range": (2000, 8000),
        "channel": "in_person",
        "card_present": True,
        "examples": 40
    },
    "merchant_category_fraud": {
        "description": "Transaction at high-risk merchant category at unusual time",
        "amount_range": (200, 1500),
        "channel": "online",
        "card_present": False,
        "examples": 40
    }
}


def generate_normal_transaction():
    return {
        "tx_id": fake.uuid4(),
        "ts": fake.date_time_between(start_date="-30d", end_date="now"),
        "amount": round(random.lognormvariate(3.5, 0.8), 2),
        "currency": "AUD",
        "merchant_name": fake.company(),
        "merchant_category": random.choice(MERCHANT_CATEGORIES),
        "country": "AU",
        "channel": random.choice(["in_person", "online", "in_person", "in_person"]),
        "card_present": random.choice([True, True, True, False]),
        "hour_of_day": random.choices(range(24), weights=[1]*7 + [3]*15 + [1]*2)[0],
        "distance_from_home_km": round(random.expovariate(1/15), 1),
        "label": "normal"
    }


def generate_fraud_example(archetype_name, archetype):
    return {
        "tx_id": fake.uuid4(),
        "ts": fake.date_time_between(start_date="-90d", end_date="-1d"),
        "amount": round(random.uniform(*archetype["amount_range"]), 2),
        "currency": "AUD",
        "merchant_name": fake.company(),
        "merchant_category": random.choice(MERCHANT_CATEGORIES),
        "country": random.choice(["NG", "RU", "BR", "AU"]) if "foreign" in archetype_name else "AU",
        "channel": archetype["channel"],
        "card_present": archetype["card_present"],
        "hour_of_day": random.choice([2, 3, 4, 23]) if "account" in archetype_name or "merchant" in archetype_name else random.randint(0, 23),
        "distance_from_home_km": round(random.uniform(2000, 15000), 1) if "foreign" in archetype_name else round(random.expovariate(1/15), 1),
        "archetype": archetype_name,
        "archetype_description": archetype["description"],
        "label": "fraud"
    }


def main():
    db = get_db()
    db.transactions.delete_many({})
    db.fraud_examples.delete_many({})

    # Generate fraud examples
    fraud_examples = []
    for name, arc in FRAUD_ARCHETYPES.items():
        for _ in range(arc["examples"]):
            fraud_examples.append(generate_fraud_example(name, arc))

    # Embed in batches of 50
    print(f"Embedding {len(fraud_examples)} fraud examples...")
    for i in range(0, len(fraud_examples), 50):
        batch = fraud_examples[i:i+50]
        texts = [transaction_to_text(tx) for tx in batch]
        embeddings = embed_texts(texts, input_type="document")
        for tx, emb in zip(batch, embeddings):
            tx["embedding"] = emb

    db.fraud_examples.insert_many(fraud_examples)
    print(f"Inserted {len(fraud_examples)} fraud examples.")

    # Generate normal transactions (no embeddings needed for these — they're queried, not corpus)
    normal = [generate_normal_transaction() for _ in range(10000)]
    db.transactions.insert_many(normal)
    print(f"Inserted {len(normal)} normal transactions.")


if __name__ == "__main__":
    main()
```

Run it:

```bash
python -m data.generate
```

Wait ~30 seconds for the vector index to build after the inserts complete. Verify in the Atlas UI under Atlas Search.

---

## Phase 3: Anomaly Scoring Service (45 min)

### 3.1 Scoring logic (`src/core.py`)

```python
from src.db import get_db
from src.embed import embed_texts, transaction_to_text

ANOMALY_THRESHOLD = 0.78  # cosine similarity above this is suspicious


def score_transaction(tx: dict) -> dict:
    """Score a transaction against the fraud corpus. Returns enriched tx with risk info."""
    db = get_db()
    text = transaction_to_text(tx)
    [embedding] = embed_texts([text], input_type="query")

    pipeline = [
        {
            "$vectorSearch": {
                "index": "fraud_vector_idx",
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": 100,
                "limit": 3
            }
        },
        {
            "$project": {
                "_id": 0,
                "archetype": 1,
                "archetype_description": 1,
                "merchant_category": 1,
                "amount": 1,
                "country": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]

    matches = list(db.fraud_examples.aggregate(pipeline))
    avg_score = sum(m["score"] for m in matches) / len(matches) if matches else 0.0

    return {
        **tx,
        "risk_score": round(avg_score, 4),
        "is_anomaly": avg_score >= ANOMALY_THRESHOLD,
        "matched_archetypes": matches
    }
```

### 3.2 Quick smoke test

Create `scripts/test_scoring.py`:

```python
from src.core import score_transaction
from datetime import datetime

# A clearly suspicious transaction
suspicious = {
    "tx_id": "test-001",
    "ts": datetime.now(),
    "amount": 1250.00,
    "currency": "AUD",
    "merchant_name": "QuickCash Online",
    "merchant_category": "online_retail",
    "country": "RU",
    "channel": "online",
    "card_present": False,
    "hour_of_day": 3,
    "distance_from_home_km": 14000
}

result = score_transaction(suspicious)
print(f"Risk score: {result['risk_score']}")
print(f"Is anomaly: {result['is_anomaly']}")
for m in result["matched_archetypes"]:
    print(f"  - {m['archetype']} (score: {m['score']:.3f}): {m['archetype_description']}")
```

Run it — you should see a risk score above 0.78 and the matched archetype should be `foreign_cnp`. If not, your vector index hasn't finished building; wait 60 seconds and retry.

---

## Phase 4: Live Streamlit Dashboard (60 min)

### 4.1 The dashboard (`app.py`)

```python
import streamlit as st
import time
import random
from datetime import datetime
from data.generate import generate_normal_transaction, generate_fraud_example, FRAUD_ARCHETYPES
from src.core import score_transaction

st.set_page_config(page_title="Fraud Detection Demo", layout="wide")
st.title("🛡️ Real-Time Transaction Anomaly Detection")
st.caption("Powered by MongoDB Atlas Vector Search")

# Sidebar controls
st.sidebar.header("Demo Controls")
inject_fraud = st.sidebar.button("💉 Inject Fraud Transaction")
fraud_type = st.sidebar.selectbox("Fraud archetype", list(FRAUD_ARCHETYPES.keys()))
auto_stream = st.sidebar.checkbox("Auto-stream normal transactions", value=True)

# State
if "feed" not in st.session_state:
    st.session_state.feed = []
if "alerts" not in st.session_state:
    st.session_state.alerts = []

col_feed, col_alerts = st.columns([2, 1])
col_feed.subheader("Live transaction feed")
col_alerts.subheader("🚨 Anomaly alerts")

feed_placeholder = col_feed.empty()
alerts_placeholder = col_alerts.empty()


def render():
    with feed_placeholder.container():
        for tx in reversed(st.session_state.feed[-20:]):
            badge = "🔴" if tx["is_anomaly"] else "🟢"
            st.markdown(
                f"{badge} `{tx['ts'].strftime('%H:%M:%S')}` "
                f"**{tx['merchant_name']}** ({tx['merchant_category']}) "
                f"{tx['amount']:.2f} {tx['currency']} — "
                f"risk: `{tx['risk_score']:.3f}`"
            )

    with alerts_placeholder.container():
        for alert in reversed(st.session_state.alerts[-10:]):
            with st.expander(
                f"⚠️ {alert['merchant_name']} — {alert['amount']:.2f} {alert['currency']} "
                f"(risk {alert['risk_score']:.3f})"
            ):
                st.write(f"**Country:** {alert['country']}")
                st.write(f"**Time:** {alert['hour_of_day']:02d}:00")
                st.write(f"**Channel:** {alert['channel']}")
                st.write("**Matched fraud archetypes:**")
                for m in alert["matched_archetypes"]:
                    st.write(f"- *{m['archetype']}* (similarity: {m['score']:.3f})")
                    st.caption(m["archetype_description"])


# Handle inject button
if inject_fraud:
    arc = FRAUD_ARCHETYPES[fraud_type]
    tx = generate_fraud_example(fraud_type, arc)
    tx["ts"] = datetime.now()
    scored = score_transaction(tx)
    st.session_state.feed.append(scored)
    if scored["is_anomaly"]:
        st.session_state.alerts.append(scored)

# Auto-stream loop
if auto_stream:
    tx = generate_normal_transaction()
    tx["ts"] = datetime.now()
    scored = score_transaction(tx)
    st.session_state.feed.append(scored)
    if scored["is_anomaly"]:
        st.session_state.alerts.append(scored)

render()

if auto_stream:
    time.sleep(2)
    st.rerun()
```

### 4.2 Launch

```bash
streamlit run app.py
```

You should see normal transactions flowing in green every two seconds. Hit "Inject Fraud Transaction" with `foreign_cnp` selected — within ~500ms you should see a red row in the feed and a new alert in the right panel with the matched archetype.

---

## Phase 5: Demo Polish (30 min)

- Tune `ANOMALY_THRESHOLD` if too many normal transactions trigger alerts (raise to 0.82) or too few fraud injections trigger alerts (lower to 0.74)
- Pre-seed the feed with 10–15 normal transactions before the demo so it doesn't look empty
- Test all five fraud archetypes — the most visually compelling are `foreign_cnp` and `account_takeover`
- Confirm latency: each scored transaction should appear within 1 second of generation

---

## Demo Script

**[1 min] Set the scene.**
"Every bank in this region runs a fraud rules engine. The team maintaining it is constantly chasing patterns that just emerged. Today I want to show what fraud detection looks like when the database itself can compare incoming transactions against known patterns by meaning, not by rule."

**[30 sec] Show the architecture.**
"This is one MongoDB cluster. It holds the operational transactions, the labeled fraud examples, and the vector embeddings — all in the same place. There is no separate vector database, no embedding pipeline, no synchronization layer."

**[1 min] Show the live feed running normally.**
Let normal transactions flow for 30 seconds. "Each of these is being scored against 200 known fraud examples in real time. Most look like normal customer activity — they go through green."

**[30 sec] Inject the demo fraud.**
Click "Inject Fraud Transaction" with `foreign_cnp`. Within a second, the dashboard lights up red. "That transaction came in 4 milliseconds ago. The system has already flagged it as anomalous *and* told us why — it matched three known card-not-present foreign-origin patterns with a similarity score of 0.84."

**[1 min] Show explainability.**
Expand the alert. "This is what the fraud team gets — not a black-box yes-or-no, but the actual patterns this transaction resembles. They can investigate with context."

**[30 sec] Land the architectural point.**
"The reason we can show you this in an afternoon is that everything is in one platform. If we had to rebuild this on a relational database plus a vector database plus an embedding pipeline, we wouldn't be having this demo today — we'd be three months in."

---

## Troubleshooting

**Vector index returns no matches.**
The index hasn't finished building. Check Atlas UI → Atlas Search; status should be "Active." Wait 60 seconds and retry.

**Voyage AI rate limit errors during data generation.**
The free tier rate-limits at ~3 requests/sec. Add `time.sleep(0.5)` between batches in `generate.py`, or upgrade to a paid tier.

**Streamlit dashboard freezes or stutters.**
The auto-rerun every 2 seconds with synchronous scoring can stack up. Reduce to one transaction every 4 seconds, or move scoring to a background thread.

**All transactions score above threshold.**
The fraud corpus may have been embedded with `input_type="query"` instead of `"document"`. Re-run `data/generate.py` and confirm the helper uses `"document"` for corpus.

---

## Cleanup

```bash
# In Atlas UI: pause or terminate the cluster
# In your shell:
deactivate
rm -rf poc-fraud-detection
```

Revoke Network Access entries that opened your firewall (`0.0.0.0/0`).

---

## Variations

- **Add a "card present" filter** to the vector search to only match patterns of the same channel — improves precision
- **Stream from a Kafka source** via Atlas Stream Processing instead of synthetic generation — more realistic for enterprise demos
- **Persist alerts** with reviewer disposition (false positive / confirmed) and use that feedback to grow the corpus over time
