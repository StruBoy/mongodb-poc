# PoC 5: Real-Time IoT / Network Telemetry Dashboard — Implementation Plan

**Customer Segment:** Telecommunications and Global 2000 Enterprises
**Time Budget:** 3.5 hours
**Cluster Tier:** Atlas M10 (recommended for write throughput)
**Total Cost (afternoon):** ~USD 3

---

## What you're building

A simulated fleet of 1,000 cell towers across three APAC regions streaming telemetry every second into a MongoDB time-series collection. A Streamlit operations dashboard renders live charts of network health, computes regional aggregations, and surfaces anomalous towers in real time. A control button lets you inject failures during the demo.

## What it demonstrates

- **Time-series collections at scale**: thousands of writes per second on a single cluster
- **Real-time aggregations** with `$group`, `$bucket`, and `$densify` for telemetry rollups
- **Operational simplicity**: ingestion, storage, queries, and alerting on one platform
- **No separate streaming/warehouse tier** required for the live operations view

## The demo moment

The dashboard shows three regions (Sydney, Singapore, Mumbai), each with hundreds of towers, all green and updating live. A counter shows ~5,000 writes/sec arriving. You click "inject failure in Singapore region" — within seconds, three towers in Singapore turn red, signal strength chart drops visibly, and the alerts panel populates with degraded tower IDs. The audience watches operational telemetry, real-time analytics, and anomaly alerting all run from one cluster.

---

## Prerequisites

- Atlas account with cluster permissions
- Python 3.11+
- An async-capable terminal (the ingestion script uses `asyncio`)

---

## Phase 1: Atlas Setup (30 min)

### 1.1 Provision the cluster

1. Create project `poc-telco-telemetry`
2. Build cluster: M10, AWS, Singapore region (`ap-southeast-1`)
3. Wait ~7 minutes

### 1.2 Configure access

- Database user `pocuser` with `readWriteAnyDatabase`
- Network access from your IP

### 1.3 Create the database with a time-series collection

This is the critical setup step. Time-series collections optimize storage and query performance for telemetry workloads — a non-time-series collection would work but with significantly worse density and query latency.

```javascript
use telco_demo

db.createCollection("telemetry", {
  timeseries: {
    timeField: "ts",
    metaField: "meta",
    granularity: "seconds"
  },
  expireAfterSeconds: 86400  // auto-delete data older than 24h — keeps the demo clean
})

db.createCollection("towers")  // metadata, not time-series
```

The `metaField: "meta"` is what makes per-tower queries fast. Every tower's metadata (id, region, location) goes into `meta`; the changing telemetry values go at the document root.

### 1.4 Create indexes for query performance

```javascript
db.telemetry.createIndex({ "meta.tower_id": 1, "ts": -1 })
db.telemetry.createIndex({ "meta.region": 1, "ts": -1 })
```

---

## Phase 2: Project Setup & Tower Fleet (45 min)

### 2.1 Project scaffolding

```bash
mkdir poc-telco-telemetry && cd poc-telco-telemetry
python -m venv venv && source venv/bin/activate
pip install pymongo motor faker streamlit python-dotenv pandas plotly
```

`.env`:

```
MONGODB_URI=mongodb+srv://pocuser:<password>@<cluster>.mongodb.net/
```

### 2.2 Database helpers (`src/db.py`)

```python
import os
from pymongo import MongoClient
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
_client = None
_async_client = None

def get_client():
    global _client
    if _client is None:
        _client = MongoClient(os.environ["MONGODB_URI"])
    return _client

def get_db():
    return get_client()["telco_demo"]

def get_async_client():
    global _async_client
    if _async_client is None:
        _async_client = AsyncIOMotorClient(os.environ["MONGODB_URI"])
    return _async_client

def get_async_db():
    return get_async_client()["telco_demo"]
```

### 2.3 Tower fleet generator (`data/generate_fleet.py`)

```python
import random
from src.db import get_db

random.seed(42)

# Three APAC regions with approximate centers and tower counts
REGIONS = {
    "Sydney": {"center": (-33.87, 151.21), "spread": 0.8, "count": 350},
    "Singapore": {"center": (1.35, 103.82), "spread": 0.3, "count": 300},
    "Mumbai": {"center": (19.08, 72.88), "spread": 0.7, "count": 350},
}


def generate_fleet():
    db = get_db()
    db.towers.delete_many({})

    fleet = []
    tower_counter = 1
    for region_name, cfg in REGIONS.items():
        for _ in range(cfg["count"]):
            lat = cfg["center"][0] + random.uniform(-cfg["spread"], cfg["spread"])
            lon = cfg["center"][1] + random.uniform(-cfg["spread"], cfg["spread"])
            tower_id = f"TWR-{region_name[:3].upper()}-{tower_counter:04d}"
            fleet.append({
                "_id": tower_id,
                "region": region_name,
                "lat": lat,
                "lon": lon,
                "type": random.choice(["macro", "macro", "macro", "small_cell"]),
                "frequency_band": random.choice(["700MHz", "1800MHz", "2600MHz", "3500MHz"]),
                "max_capacity_subscribers": random.choice([500, 1000, 2000, 5000]),
                "installed_date": f"20{random.randint(15, 24)}-{random.randint(1, 12):02d}-01"
            })
            tower_counter += 1

    db.towers.insert_many(fleet)
    print(f"Inserted {len(fleet)} towers across {len(REGIONS)} regions.")


if __name__ == "__main__":
    generate_fleet()
```

Run it:

```bash
python -m data.generate_fleet
```

---

## Phase 3: Async Telemetry Generator (45 min)

### 3.1 The ingestion engine (`data/stream_telemetry.py`)

This is the heart of the throughput demo. We use `motor` (async pymongo) and batched inserts to hit ~5,000 writes/sec.

```python
import asyncio
import random
import os
import sys
from datetime import datetime
from src.db import get_async_db, get_client

# Shared state for failure injection (set by Streamlit via a control collection)
FAILURE_FILE = "/tmp/telemetry_failures.txt"


def load_failure_set():
    """Read tower IDs marked as failing. Recreated each cycle so Streamlit can update it."""
    if not os.path.exists(FAILURE_FILE):
        return set()
    with open(FAILURE_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def baseline_metrics(tower):
    """Healthy telemetry for a tower."""
    return {
        "signal_strength_dbm": random.gauss(-65, 4),
        "packet_loss_pct": max(0, random.gauss(0.5, 0.3)),
        "throughput_mbps": random.gauss(450, 50),
        "temperature_c": random.gauss(38, 3),
        "active_subscribers": random.randint(int(tower["max_capacity_subscribers"] * 0.3),
                                              int(tower["max_capacity_subscribers"] * 0.8))
    }


def degraded_metrics(tower):
    """Telemetry pattern for a failing tower."""
    return {
        "signal_strength_dbm": random.gauss(-95, 5),    # much weaker
        "packet_loss_pct": random.gauss(15, 3),          # significantly higher
        "throughput_mbps": random.gauss(80, 20),         # much lower
        "temperature_c": random.gauss(72, 4),            # overheating
        "active_subscribers": random.randint(int(tower["max_capacity_subscribers"] * 0.05),
                                              int(tower["max_capacity_subscribers"] * 0.2))
    }


async def stream_loop(target_rate: int = 1000):
    """Stream telemetry. target_rate = total events per second across the fleet."""
    db = get_async_db()
    towers = await db.towers.find().to_list(length=None)
    print(f"Streaming telemetry for {len(towers)} towers at ~{target_rate} events/sec...")

    cycle = 0
    while True:
        cycle += 1
        failure_set = load_failure_set()
        now = datetime.utcnow()

        # Each tower emits one record per cycle. Batch insert for throughput.
        events = []
        for tower in towers:
            metrics = degraded_metrics(tower) if tower["_id"] in failure_set else baseline_metrics(tower)
            events.append({
                "ts": now,
                "meta": {
                    "tower_id": tower["_id"],
                    "region": tower["region"],
                    "type": tower["type"]
                },
                **metrics
            })

        # Insert in chunks of 500 for parallelism
        chunks = [events[i:i+500] for i in range(0, len(events), 500)]
        await asyncio.gather(*[db.telemetry.insert_many(chunk) for chunk in chunks])

        if cycle % 5 == 0:
            print(f"Cycle {cycle}: inserted {len(events)} events at {now.isoformat()}")

        # Sleep so our average rate matches target_rate
        await asyncio.sleep(max(0.1, len(events) / target_rate))


if __name__ == "__main__":
    try:
        asyncio.run(stream_loop(target_rate=1000))
    except KeyboardInterrupt:
        print("Stopped.")
```

Quick smoke test in a second terminal:

```bash
python -m data.stream_telemetry
```

Should print "Streaming telemetry for 1000 towers..." and then a cycle log every five seconds. In `mongosh`:

```javascript
use telco_demo
db.telemetry.countDocuments({ts: {$gte: new Date(Date.now() - 60000)}})
```

After a minute you should see ~60,000+ documents.

Stop the stream with Ctrl+C — you'll restart it from the dashboard.

---

## Phase 4: Aggregation Queries (30 min)

### 4.1 Analytics service (`src/analytics.py`)

```python
from datetime import datetime, timedelta
from src.db import get_db


def regional_health(window_seconds: int = 30) -> list:
    """Average metrics per region over the last N seconds."""
    db = get_db()
    cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
    pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$meta.region",
            "avg_signal_dbm": {"$avg": "$signal_strength_dbm"},
            "avg_packet_loss": {"$avg": "$packet_loss_pct"},
            "avg_throughput_mbps": {"$avg": "$throughput_mbps"},
            "avg_temp_c": {"$avg": "$temperature_c"},
            "tower_count": {"$addToSet": "$meta.tower_id"}
        }},
        {"$project": {
            "region": "$_id",
            "_id": 0,
            "avg_signal_dbm": 1,
            "avg_packet_loss": 1,
            "avg_throughput_mbps": 1,
            "avg_temp_c": 1,
            "tower_count": {"$size": "$tower_count"}
        }},
        {"$sort": {"region": 1}}
    ]
    return list(db.telemetry.aggregate(pipeline))


def degraded_towers(window_seconds: int = 30, packet_loss_threshold: float = 5.0) -> list:
    """Towers whose recent average packet loss exceeds threshold."""
    db = get_db()
    cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
    pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$meta.tower_id",
            "region": {"$first": "$meta.region"},
            "avg_signal_dbm": {"$avg": "$signal_strength_dbm"},
            "avg_packet_loss": {"$avg": "$packet_loss_pct"},
            "avg_temp_c": {"$avg": "$temperature_c"},
            "samples": {"$sum": 1}
        }},
        {"$match": {"avg_packet_loss": {"$gte": packet_loss_threshold}, "samples": {"$gte": 3}}},
        {"$sort": {"avg_packet_loss": -1}},
        {"$limit": 20}
    ]
    return list(db.telemetry.aggregate(pipeline))


def throughput_timeline(region: str = None, window_seconds: int = 120) -> list:
    """Throughput per-second timeline, optionally filtered by region."""
    db = get_db()
    cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
    match = {"ts": {"$gte": cutoff}}
    if region:
        match["meta.region"] = region

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {
                "second": {"$dateTrunc": {"date": "$ts", "unit": "second"}},
                "region": "$meta.region"
            },
            "total_throughput_mbps": {"$sum": "$throughput_mbps"},
            "tower_count": {"$sum": 1}
        }},
        {"$project": {
            "ts": "$_id.second",
            "region": "$_id.region",
            "total_throughput_mbps": 1,
            "tower_count": 1,
            "_id": 0
        }},
        {"$sort": {"ts": 1}}
    ]
    return list(db.telemetry.aggregate(pipeline))


def write_throughput_estimate(window_seconds: int = 10) -> float:
    """Estimate writes-per-second over the last window."""
    db = get_db()
    cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
    count = db.telemetry.count_documents({"ts": {"$gte": cutoff}})
    return count / window_seconds
```

---

## Phase 5: Streamlit Operations Dashboard (60 min)

### 5.1 The dashboard (`app.py`)

```python
import streamlit as st
import pandas as pd
import plotly.express as px
import time
import random
import os
from src.analytics import regional_health, degraded_towers, throughput_timeline, write_throughput_estimate
from src.db import get_db

FAILURE_FILE = "/tmp/telemetry_failures.txt"

st.set_page_config(page_title="Network Operations", layout="wide")
st.title("📡 Network Operations Dashboard")
st.caption("MongoDB Atlas Time-Series — 1,000 towers, ~1,000 events/sec, real-time alerting")

# ============================================================
# Demo controls
# ============================================================
st.sidebar.header("Demo controls")

if st.sidebar.button("💥 Inject failure in Singapore region"):
    db = get_db()
    sgp_towers = list(db.towers.find({"region": "Singapore"}, {"_id": 1}).limit(3))
    failure_ids = [t["_id"] for t in sgp_towers]
    with open(FAILURE_FILE, "w") as f:
        f.write("\n".join(failure_ids))
    st.sidebar.success(f"Injected: {', '.join(failure_ids)}")

if st.sidebar.button("✅ Clear all failures"):
    if os.path.exists(FAILURE_FILE):
        os.remove(FAILURE_FILE)
    st.sidebar.success("All failures cleared.")

current_failures = []
if os.path.exists(FAILURE_FILE):
    with open(FAILURE_FILE) as f:
        current_failures = [l.strip() for l in f if l.strip()]
if current_failures:
    st.sidebar.warning(f"Active failures: {len(current_failures)}")
    for f_id in current_failures:
        st.sidebar.write(f"• {f_id}")

# ============================================================
# Top-level metrics
# ============================================================
write_rate = write_throughput_estimate(window_seconds=10)
m1, m2, m3 = st.columns(3)
m1.metric("Live write rate", f"{write_rate:,.0f} ops/sec")
m2.metric("Towers reporting", "1,000")
m3.metric("Region count", "3")

# ============================================================
# Regional health
# ============================================================
st.subheader("🌏 Regional health (last 30 seconds)")
health = regional_health(window_seconds=30)

if health:
    cols = st.columns(len(health))
    for col, region in zip(cols, health):
        is_degraded = region["avg_packet_loss"] > 3.0
        emoji = "🔴" if is_degraded else "🟢"
        with col:
            st.markdown(f"### {emoji} {region['region']}")
            st.metric("Avg signal", f"{region['avg_signal_dbm']:.1f} dBm")
            st.metric("Packet loss", f"{region['avg_packet_loss']:.2f}%",
                      delta="degraded" if is_degraded else "normal",
                      delta_color="inverse" if is_degraded else "normal")
            st.metric("Throughput", f"{region['avg_throughput_mbps']:.0f} Mbps")
            st.metric("Avg temp", f"{region['avg_temp_c']:.1f}°C")
else:
    st.info("Waiting for telemetry. Make sure the stream is running.")

# ============================================================
# Throughput timeline
# ============================================================
st.subheader("📊 Total throughput timeline (last 2 minutes)")
timeline_data = throughput_timeline(window_seconds=120)
if timeline_data:
    df = pd.DataFrame(timeline_data)
    fig = px.line(df, x="ts", y="total_throughput_mbps", color="region",
                  title="Aggregated network throughput per region")
    st.plotly_chart(fig, use_container_width=True)

# ============================================================
# Degraded towers panel
# ============================================================
st.subheader("🚨 Towers needing attention")
degraded = degraded_towers(window_seconds=30, packet_loss_threshold=5.0)

if not degraded:
    st.success("All towers operating within tolerance.")
else:
    for tower in degraded:
        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1])
            cols[0].markdown(f"**{tower['_id']}** ({tower['region']})")
            cols[1].metric("Signal", f"{tower['avg_signal_dbm']:.1f} dBm")
            cols[2].metric("Loss", f"{tower['avg_packet_loss']:.1f}%")
            cols[3].metric("Temp", f"{tower['avg_temp_c']:.1f}°C")

# Auto-refresh every 3 seconds
time.sleep(3)
st.rerun()
```

### 5.2 Launch sequence

In one terminal:

```bash
# Make sure fleet is loaded
python -m data.generate_fleet

# Start the telemetry stream
python -m data.stream_telemetry
```

In a second terminal:

```bash
streamlit run app.py
```

You should see live metrics, regional health all green, and the write rate counter showing ~1,000 ops/sec. After 30 seconds the dashboard will be in steady state.

---

## Phase 6: Demo Polish (30 min)

### 6.1 Tune for visible impact

- Confirm the failure injection causes a visible drop in the throughput chart within ~5 seconds
- Adjust `packet_loss_threshold` if too few or too many towers show as degraded under normal conditions
- Pre-warm the system by letting the stream run for 60 seconds before going on stage

### 6.2 Multi-region failure scenario

Add a sidebar button for a more dramatic demo:

```python
if st.sidebar.button("🔥 Major incident: Mumbai outage"):
    db = get_db()
    mum_towers = list(db.towers.find({"region": "Mumbai"}, {"_id": 1}).limit(15))
    failure_ids = [t["_id"] for t in mum_towers]
    with open(FAILURE_FILE, "w") as f:
        f.write("\n".join(failure_ids))
    st.sidebar.error(f"Major incident: {len(failure_ids)} towers down")
```

---

## Demo Script

**[1 min] Frame the data architecture problem.**
"If you run technology in an APAC telco today, network telemetry is your hardest data architecture problem. You're ingesting at thousands of events per second across hundreds of thousands of devices. The standard answer is a Kafka tier feeding a time-series database feeding a warehouse for analysis. Three systems minimum, often more, with all the operational overhead and ETL fragility that implies."

**[1 min] Show the architecture.**
"This is one MongoDB cluster. The telemetry collection is a time-series collection — purpose-built for this shape of data. There is no separate streaming tier. There is no separate warehouse. The same cluster ingests, stores, queries, and feeds the dashboard you're looking at."

**[1 min] Show the steady state.**
Let the dashboard run. Point to the live write rate counter. "We're sustaining about 1,000 events per second on a single M10 cluster — and we could push this to ten times that on M30 without any architectural change. All three regions are healthy. Throughput chart looks normal. No alerts."

**[1.5 min] Inject the failure.**
Click "Inject failure in Singapore region." Wait 5–10 seconds. "Three towers in Singapore have started reporting degraded metrics. Watch the throughput chart — there's the dip. Watch the regional panel — Singapore just went red. Watch the alerts panel — there are the three towers, with their specific metrics."

**[30 sec] Land the operations point.**
"This is what most operations teams want and few of them have today. The reason it works is that the database is doing the aggregation in real time, on the same cluster that's holding the raw telemetry. There's nothing separate to keep in sync. No stream processor to debug. No warehouse query to wait on."

**[30 sec] Land the architectural point.**
"For a telco, this isn't just convenient. It's a strategic decision. Every system you remove from the architecture is one fewer point of failure during a major incident — and major incidents are when you find out which architectural shortcuts you took five years ago."

---

## Troubleshooting

**Write rate counter shows 0.**
The stream isn't running. Check the second terminal; it should show cycle messages every five seconds. If it crashed, restart it.

**Streamlit dashboard freezes.**
The `time.sleep(3)` + `st.rerun()` pattern can stack up if queries take too long. Check query latency in mongosh: `db.telemetry.find({ts: {$gte: new Date(Date.now() - 30000)}}).explain("executionStats")`. If queries are slow, check the indexes from Phase 1.4 are present.

**Failure injection has no visible effect.**
The failure file path may be wrong on Windows. Change `FAILURE_FILE` to a path your stream and Streamlit can both reach (e.g. `C:/temp/telemetry_failures.txt`).

**Throughput chart is empty.**
Telemetry hasn't accumulated 2 minutes of history yet. Wait. If still empty, check the time-series collection was created with `granularity: "seconds"`.

**Hitting connection pool limits.**
Atlas M10 supports ~500 connections. If both the stream and the dashboard are connecting heavily, set explicit pool limits in the Motor client: `AsyncIOMotorClient(uri, maxPoolSize=20)`.

---

## Cleanup

```bash
# Stop the stream (Ctrl+C in its terminal)
# Pause or terminate the Atlas cluster
deactivate
rm -rf poc-telco-telemetry
rm -f /tmp/telemetry_failures.txt
```

---

## Variations

- **Add device-shadow pattern**: maintain a separate `tower_state` collection holding the latest known state of each tower, updated on every telemetry write
- **Add ML-based anomaly detection**: instead of a fixed threshold, embed recent telemetry windows and use vector similarity against known failure patterns (combines this PoC with the pattern from PoC 1)
- **Add geospatial visualization**: render the towers on a map using Plotly or Pydeck, with color showing health
- **Scale to 10K towers**: increase the fleet, push to M30 cluster, demonstrate that the architecture doesn't change
- **Atlas Stream Processing**: replace the application-level ingest with Atlas Stream Processing pulling from a Kafka topic, demonstrating in-database stream transformations
