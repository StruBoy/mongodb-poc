"""Streamlit operations dashboard for the iot-telemetry PoC.

Renders:
  - Top metrics: live write rate, fleet size, district count, active failures
  - Regional health panel (one card per Singapore district)
  - Throughput timeline (per-district lines, last 2 minutes)
  - Tower map (Plotly scatter, colour-coded by current health state)
  - Degraded towers list
  - Sidebar controls: per-district failure injection, major-incident button,
    clear-failures, refresh interval

Failure-injection state lives in telco_demo.control_failures, read by the
streamer at the start of every cycle. No /tmp file shared between processes.

Run from the project root (iot-telemetry/):
    streamlit run app.py

The streamer must already be running:
    python -m data.stream_telemetry
"""
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics import (
    degraded_towers,
    regional_health,
    throughput_timeline,
    write_throughput_estimate,
)
from src.db import get_db


REGION_COLOURS = {
    "Central": "#1f77b4",
    "East": "#2ca02c",
    "West": "#d62728",
}
PACKET_LOSS_ALERT_PCT = 3.0    # district turns red above this
TOWER_PACKET_LOSS_THRESHOLD = 5.0


st.set_page_config(page_title="Singapore Network Operations", layout="wide")
st.title("📡 Singapore Network Operations")
st.caption(
    "MongoDB Atlas time-series + real-time aggregations · "
    "1,000 towers across Central / East / West · ~1,000 events/sec"
)

db = get_db()


# ---------------------------------------------------------------------------
# Sidebar — demo controls
# ---------------------------------------------------------------------------
st.sidebar.header("Demo controls")

refresh_seconds = st.sidebar.slider(
    "Refresh interval (sec)", 1.0, 10.0, 3.0, 0.5,
    help="How often the dashboard re-runs its aggregations.",
)

st.sidebar.markdown("---")
st.sidebar.subheader("Inject failure")
st.sidebar.caption("Writes to control_failures. The streamer picks it up on the next cycle.")


def inject(region: str, count: int, kind: str):
    """Insert `count` failures in `region` into telco_demo.control_failures."""
    targets = list(db.towers.find(
        {"region": region, "_id": {"$nin": _current_failure_ids()}},
        {"_id": 1},
    ).limit(count))
    if not targets:
        st.sidebar.warning(f"No clean towers left in {region} — clear failures first.")
        return
    now = datetime.now(timezone.utc)
    db.control_failures.insert_many([
        {"_id": t["_id"], "injected_at": now, "kind": kind, "region": region}
        for t in targets
    ])
    st.sidebar.success(f"Injected {len(targets)} failures in {region}.")


def _current_failure_ids() -> list[str]:
    return [doc["_id"] for doc in db.control_failures.find({}, {"_id": 1})]


col_a, col_b, col_c = st.sidebar.columns(3)
if col_a.button("Central", width="stretch"):
    inject("Central", 3, "manual_central")
    st.rerun()
if col_b.button("East", width="stretch"):
    inject("East", 3, "manual_east")
    st.rerun()
if col_c.button("West", width="stretch"):
    inject("West", 3, "manual_west")
    st.rerun()

if st.sidebar.button("🔥 Major incident: 15 towers in West", width="stretch"):
    inject("West", 15, "major_incident_west")
    st.rerun()

if st.sidebar.button("✅ Clear all failures", width="stretch"):
    db.control_failures.delete_many({})
    st.rerun()

current_failures = list(db.control_failures.find().sort("injected_at", -1))
if current_failures:
    st.sidebar.markdown("---")
    st.sidebar.warning(f"Active failures: {len(current_failures)}")
    for f in current_failures[:10]:
        st.sidebar.write(f"• `{f['_id']}` ({f.get('region', '?')})")
    if len(current_failures) > 10:
        st.sidebar.caption(f"…and {len(current_failures) - 10} more")


# ---------------------------------------------------------------------------
# Top-line metrics
# ---------------------------------------------------------------------------
write_rate = write_throughput_estimate(window_seconds=10)
fleet_size = db.towers.estimated_document_count()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Live write rate", f"{write_rate:,.0f} ops/sec")
m2.metric("Towers reporting", f"{fleet_size:,}")
m3.metric("Districts", "3")
m4.metric(
    "Active failures",
    f"{len(current_failures)}",
    delta="incident" if current_failures else "all clear",
    delta_color="inverse" if current_failures else "normal",
)

if write_rate < 50:
    st.warning(
        "⚠️ Write rate is near zero — the telemetry stream isn't running. "
        "Start it in another terminal: `python -m data.stream_telemetry`"
    )


# ---------------------------------------------------------------------------
# Regional health
# ---------------------------------------------------------------------------
st.subheader("🌏 District health (last 30 seconds)")

health = regional_health(window_seconds=30)
if not health:
    st.info("Waiting for telemetry to accumulate…")
else:
    cols = st.columns(len(health))
    for col, region in zip(cols, health):
        is_degraded = region["avg_packet_loss"] > PACKET_LOSS_ALERT_PCT
        emoji = "🔴" if is_degraded else "🟢"
        with col:
            st.markdown(f"### {emoji} {region['region']}")
            st.metric("Avg signal", f"{region['avg_signal_dbm']:.1f} dBm")
            st.metric(
                "Packet loss",
                f"{region['avg_packet_loss']:.2f}%",
                delta="degraded" if is_degraded else "nominal",
                delta_color="inverse" if is_degraded else "normal",
            )
            st.metric("Avg throughput", f"{region['avg_throughput_mbps']:.0f} Mbps")
            st.metric("Avg temperature", f"{region['avg_temp_c']:.1f}°C")
            st.caption(f"{region['tower_count']} towers reporting")


# ---------------------------------------------------------------------------
# Two columns: timeline (left), tower map (right)
# ---------------------------------------------------------------------------
col_chart, col_map = st.columns([3, 2])

with col_chart:
    st.subheader("📊 Aggregated throughput per district")
    timeline_data = throughput_timeline(window_seconds=120)
    if timeline_data:
        df = pd.DataFrame(timeline_data)
        fig = px.line(
            df.sort_values("ts"),
            x="ts",
            y="total_throughput_mbps",
            color="region",
            color_discrete_map=REGION_COLOURS,
            labels={"ts": "Time", "total_throughput_mbps": "Mbps", "region": "District"},
        )
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Waiting for timeline data…")


@st.cache_data(ttl=60, show_spinner=False)
def load_tower_positions():
    """Tower positions don't change during a demo — cache for one minute."""
    return list(db.towers.find({}, {"_id": 1, "region": 1, "lat": 1, "lon": 1, "type": 1}))


with col_map:
    st.subheader("🗺️ Tower fleet")
    positions = load_tower_positions()
    failing_ids = {f["_id"] for f in current_failures}
    map_df = pd.DataFrame([
        {
            "tower_id": t["_id"],
            "region": t["region"],
            "lat": t["lat"],
            "lon": t["lon"],
            "type": t["type"],
            "state": "failing" if t["_id"] in failing_ids else "healthy",
        }
        for t in positions
    ])
    fig_map = px.scatter_map(
        map_df,
        lat="lat",
        lon="lon",
        color="state",
        color_discrete_map={"healthy": "#2ca02c", "failing": "#d62728"},
        hover_name="tower_id",
        hover_data={"region": True, "type": True, "lat": False, "lon": False, "state": True},
        zoom=10,
        center={"lat": 1.32, "lon": 103.83},
        height=380,
    )
    fig_map.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend_title_text="")
    fig_map.update_traces(marker={"size": 8})
    st.plotly_chart(fig_map, width="stretch")


# ---------------------------------------------------------------------------
# Degraded towers
# ---------------------------------------------------------------------------
st.subheader("🚨 Towers needing attention")
degraded = degraded_towers(window_seconds=30, packet_loss_threshold=TOWER_PACKET_LOSS_THRESHOLD)

if not degraded:
    st.success("All towers operating within tolerance.")
else:
    st.warning(f"{len(degraded)} tower(s) above {TOWER_PACKET_LOSS_THRESHOLD}% packet loss.")
    for tower in degraded:
        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1, 1])
            cols[0].markdown(
                f"**`{tower['_id']}`** · {tower['region']} · {tower.get('type', '?')}"
            )
            cols[1].metric("Signal", f"{tower['avg_signal_dbm']:.1f} dBm")
            cols[2].metric("Packet loss", f"{tower['avg_packet_loss']:.1f}%")
            cols[3].metric("Throughput", f"{tower['avg_throughput_mbps']:.0f} Mbps")
            cols[4].metric("Temp", f"{tower['avg_temp_c']:.1f}°C")


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
import os as _os
import time as _time
if not _os.environ.get("APP_DISABLE_AUTOREFRESH"):
    _time.sleep(refresh_seconds)
    st.rerun()
