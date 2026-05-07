"""Streamlit dashboard for the fraud-detect PoC.

Shows a live transaction feed scored against the fraud_examples corpus
via Atlas Vector Search, with explainable alerts (top matched archetype +
consensus across the top-K neighbors).

Run from the project root (fraud-detect/):
    streamlit run app.py
"""
import time
from datetime import datetime

import streamlit as st

from data.generate import FRAUD_ARCHETYPES, generate_fraud_example, generate_normal_transaction
from src.core import ANOMALY_THRESHOLD, score_transaction
from src.embed import transaction_to_text

st.set_page_config(page_title="Fraud Detection Demo", layout="wide")
st.title("🛡️ Real-Time Transaction Anomaly Detection")
st.caption("Powered by MongoDB Atlas Vector Search · voyage-3 embeddings")

# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------
st.session_state.setdefault("feed", [])
st.session_state.setdefault("alerts", [])
st.session_state.setdefault("scored_count", 0)
st.session_state.setdefault("inject_request", None)

# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
st.sidebar.header("Demo controls")

fraud_type = st.sidebar.selectbox(
    "Fraud archetype",
    list(FRAUD_ARCHETYPES.keys()),
    help="Pick which kind of fraud to inject when you click the button below.",
)

if st.sidebar.button("💉 Inject fraud transaction", use_container_width=True):
    st.session_state.inject_request = fraud_type

auto_stream = st.sidebar.toggle("Auto-stream normal transactions", value=True)
stream_interval = st.sidebar.slider(
    "Stream interval (seconds)", 1.0, 5.0, 2.0, 0.5,
    disabled=not auto_stream,
)

if st.sidebar.button("🧹 Reset feed", use_container_width=True):
    st.session_state.feed = []
    st.session_state.alerts = []
    st.session_state.scored_count = 0
    st.session_state.inject_request = None
    st.rerun()

st.sidebar.divider()
st.sidebar.subheader("Detection rules")
st.sidebar.markdown(
    f"- per-archetype best-match cosine\n"
    f"- flag when top archetype score ≥ **{ANOMALY_THRESHOLD}**"
)

st.sidebar.divider()
st.sidebar.subheader("Session stats")
total = st.session_state.scored_count
flagged = len(st.session_state.alerts)
flag_rate = (flagged / total * 100) if total else 0.0
st.sidebar.metric("Transactions scored", f"{total:,}")
st.sidebar.metric("Anomalies flagged", f"{flagged:,}", delta=f"{flag_rate:.1f}% flag rate")


# ----------------------------------------------------------------------------
# Scoring helpers
# ----------------------------------------------------------------------------
def score_and_record(tx: dict) -> dict:
    tx["ts"] = datetime.now()
    t0 = time.perf_counter()
    scored = score_transaction(tx)
    scored["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    st.session_state.feed.append(scored)
    st.session_state.scored_count += 1
    if scored["is_anomaly"]:
        st.session_state.alerts.append(scored)
    return scored


# Handle inject request before rendering so it appears in this frame
if st.session_state.inject_request:
    arc_name = st.session_state.inject_request
    arc = FRAUD_ARCHETYPES[arc_name]
    tx = generate_fraud_example(arc_name, arc)
    with st.spinner(f"Scoring injected {arc_name} transaction..."):
        score_and_record(tx)
    st.session_state.inject_request = None

# Auto-stream tick (one normal tx per render cycle)
if auto_stream:
    score_and_record(generate_normal_transaction())


# ----------------------------------------------------------------------------
# Main panel: live feed + alerts side by side
# ----------------------------------------------------------------------------
col_feed, col_alerts = st.columns([3, 2])

with col_feed:
    st.subheader("Live transaction feed")
    if not st.session_state.feed:
        st.info("Waiting for transactions...")
    else:
        for tx in reversed(st.session_state.feed[-20:]):
            badge = "🔴" if tx["is_anomaly"] else "🟢"
            arc = tx.get("top_archetype") or "—"
            gap = tx.get("score_gap", 0.0)
            st.markdown(
                f"{badge} `{tx['ts'].strftime('%H:%M:%S')}` "
                f"**{tx['merchant_name']}** · {tx['merchant_category']} · "
                f"{tx['amount']:.2f} {tx['currency']} · {tx['country']} · "
                f"risk `{tx['risk_score']:.3f}` · "
                f"top `{arc}` (gap `{gap:+.3f}`) · "
                f"`{tx['latency_ms']}ms`"
            )

with col_alerts:
    st.subheader("🚨 Anomaly alerts")
    if not st.session_state.alerts:
        st.info("No alerts yet. Inject a fraud transaction from the sidebar to see one.")
    else:
        for alert in reversed(st.session_state.alerts[-10:]):
            arc = alert.get("top_archetype") or "?"
            gap = alert.get("score_gap", 0.0)
            with st.expander(
                f"⚠️ {alert['merchant_name']} — {alert['amount']:.2f} {alert['currency']} "
                f"· {arc} · risk {alert['risk_score']:.3f}",
                expanded=False,
            ):
                c1, c2, c3 = st.columns(3)
                c1.metric("Risk score", f"{alert['risk_score']:.4f}")
                c2.metric("Margin over runner-up", f"{gap:+.4f}", delta=arc)
                c3.metric("Latency", f"{alert['latency_ms']} ms")

                st.markdown(
                    f"**Country:** {alert['country']} &nbsp;·&nbsp; "
                    f"**Hour:** {alert['hour_of_day']:02d}:00 &nbsp;·&nbsp; "
                    f"**Channel:** {alert['channel']} &nbsp;·&nbsp; "
                    f"**Card present:** {alert['card_present']} &nbsp;·&nbsp; "
                    f"**Distance from home:** {alert['distance_from_home_km']} km"
                )

                st.markdown("**Score against each fraud archetype:**")
                for m in alert["matched_archetypes"]:
                    is_top = m["archetype"] == arc
                    prefix = "🏆 " if is_top else "・"
                    st.markdown(
                        f"{prefix}`{m['archetype']}` — **{m['score']:.4f}** "
                        f"(best example: {m['country']}, {m['merchant_category']}, "
                        f"${m['amount']:.2f}, h={m['hour_of_day']:02d})"
                    )
                    if is_top:
                        st.caption(m.get("archetype_description", ""))

                with st.popover("Embedded text used for query"):
                    st.code(transaction_to_text(alert), language="text")


# ----------------------------------------------------------------------------
# Auto-rerun loop
# ----------------------------------------------------------------------------
if auto_stream:
    time.sleep(stream_interval)
    st.rerun()
