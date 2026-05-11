"""Streamlit UI for the data-residency PoC.

Layout:
  - Sidebar
      - Demo controls: 🌍 bulk migrate to natural regions / ↺ reset all to US
      - Live cluster: per-zone customer + order counters (auto-refresh)
      - Inspect: customer dropdown + shard-distribution panel
  - Main
      - World map: business locations + 3 data-centre markers + connection lines
      - Customer table (st.data_editor): edit the Data column to one of US/EU/APAC,
        Submit migrates the changed rows

Run AFTER:
    python -m scripts.create_db_index
    python -m data.generate

Then:
    streamlit run app.py
"""
from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.customers import customer_count_by_location, list_customers
from src.db import get_db
from src.migration import (
    discover_zone_to_shard,
    list_shards,
    migrate_all_to_natural_regions,
    migrate_customer,
    physical_shard_for_customer,
    reset_all_to_us,
    shard_distribution_overview,
)
from src.orders import order_count_by_location, order_count_for_customer
from src.zones import ZONE_NAMES, ZONE_REGION_INFO, zone_for_country

LIVE_REFRESH_SECONDS = 3

st.set_page_config(page_title="Global Data Residency", layout="wide")
st.title("🌍 Global Data Residency")
st.caption(
    "MongoDB Atlas Global Cluster · zone sharding across us-east-1 / eu-central-1 / ap-southeast-1 · "
    "100 customer tenants · ~7,500 orders · residency follows the `location` shard-key field."
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "last_action" not in st.session_state:
    st.session_state.last_action = None
if "selected_inspect" not in st.session_state:
    st.session_state.selected_inspect = None
if "table_version" not in st.session_state:
    # Bumping this resets the data_editor's internal state so old edits don't
    # bleed across submits.
    st.session_state.table_version = 0


# ---------------------------------------------------------------------------
# Sidebar — controls + live cluster + inspector
# ---------------------------------------------------------------------------
st.sidebar.header("Demo controls")
bulk_clicked = st.sidebar.button(
    "🌍 Migrate to natural regions",
    use_container_width=True,
    help=(
        "Move every customer to the zone matching their business country: "
        "AMER → US, EMEA → EU, APAC → APAC. Sequential, ~30s for 100 customers."
    ),
)
reset_clicked = st.sidebar.button(
    "↺ Reset all to US",
    use_container_width=True,
    help="Move everyone back to the US zone.",
)


def _bump_table_version():
    st.session_state.table_version += 1


if bulk_clicked:
    with st.spinner("Migrating customers to their natural regions…"):
        result = migrate_all_to_natural_regions()
    st.session_state.last_action = (
        f"Bulk migration complete: moved {len(result['moved'])} customers, "
        f"skipped {result['skipped']} already-in-place."
    )
    _bump_table_version()
    st.rerun()

if reset_clicked:
    with st.spinner("Resetting all customers to US zone…"):
        result = reset_all_to_us()
    st.session_state.last_action = f"Reset complete: moved {result['count']} customers back to US."
    _bump_table_version()
    st.rerun()


# Live cluster panel — refreshes on its own timer.
# The fragment is mounted inside a `with st.sidebar:` block at the call site,
# so we use plain st.markdown / st.caption inside the fragment body. Streamlit
# disallows st.sidebar.X inside an @st.fragment.
@st.fragment(run_every=LIVE_REFRESH_SECONDS)
def render_live_cluster():
    cust_counts = customer_count_by_location()
    order_counts = order_count_by_location()
    n_active = sum(1 for z in ZONE_NAMES if cust_counts.get(z, 0) > 0)
    st.markdown("### Live cluster")
    st.caption(f"Refreshes every {LIVE_REFRESH_SECONDS}s. {n_active}/3 zones active.")
    for z in ZONE_NAMES:
        info = ZONE_REGION_INFO[z]
        n_cust = cust_counts.get(z, 0)
        n_ord = order_counts.get(z, 0)
        st.markdown(
            f"<div style='border-left:4px solid {info['color']};"
            f"padding:0.4em 0.6em;margin:0.3em 0;'>"
            f"<b>{z}</b> · {info['city']} ({info['aws_region']})<br/>"
            f"<span style='font-size:0.95em'>{n_cust} customers · {n_ord:,} orders</span>"
            f"</div>",
            unsafe_allow_html=True,
        )


with st.sidebar:
    render_live_cluster()


# Discover which physical shard each zone maps to by probing one customer
# per zone via explain. Cached in session_state — re-probes only while at
# least one zone is still empty (i.e. before a bulk migration).
def _zone_shard_label(shard_name: str, zone_map: dict) -> str:
    """Return e.g. 'atlas-xu2oze-shard-1 (APAC zone · ap-southeast-1)'."""
    for zone, sname in zone_map.items():
        if sname == shard_name:
            return f"{shard_name} ({zone} zone · {ZONE_REGION_INFO[zone]['aws_region']})"
    return shard_name


if "zone_to_shard" not in st.session_state or len(st.session_state.zone_to_shard) < 3:
    st.session_state.zone_to_shard = discover_zone_to_shard()

zone_to_shard = st.session_state.zone_to_shard


# Inspector panel
st.sidebar.markdown("### Inspect")
st.sidebar.caption("Pick a customer to see which shard physically holds their docs.")
all_customers = list_customers()
if all_customers:
    inspect_options = ["—"] + [f"{c['_id']} · {c['name']}" for c in all_customers]
    inspect_choice = st.sidebar.selectbox(
        "Customer", inspect_options, key=f"inspect_{st.session_state.table_version}"
    )
    st.session_state.selected_inspect = (
        inspect_choice.split(" · ", 1)[0] if inspect_choice != "—" else None
    )
else:
    st.session_state.selected_inspect = None


def render_inspector(cust_id: str):
    cust = next((c for c in all_customers if c["_id"] == cust_id), None)
    if not cust:
        st.sidebar.warning(f"Customer {cust_id} not found.")
        return
    st.sidebar.markdown(f"**{cust['_id']}** · {cust['name']}")
    st.sidebar.caption(
        f"Business: {cust.get('city', '?')}, {cust.get('country', '?')} · "
        f"natural zone: {zone_for_country(cust.get('country', ''))}"
    )
    st.sidebar.markdown(
        f"**Logical location:** `{cust.get('location')}` ({cust.get('data_zone')} zone)"
    )
    n_orders = order_count_for_customer(cust_id)
    st.sidebar.markdown(f"**Orders:** {n_orders}")

    placement = physical_shard_for_customer(cust_id)
    cust_shards = placement.get("customer", [])
    orders_shards = placement.get("orders", [])

    st.sidebar.markdown("**Customer doc lives on:**")
    if cust_shards:
        for s in cust_shards:
            st.sidebar.markdown(f"- `{_zone_shard_label(s['shard'], zone_to_shard)}` (n={s['n']})")
    else:
        st.sidebar.markdown("_(no shard reported by explain — verify in Atlas UI)_")

    st.sidebar.markdown("**Order docs live on:**")
    if orders_shards:
        for s in orders_shards:
            st.sidebar.markdown(f"- `{_zone_shard_label(s['shard'], zone_to_shard)}` (n={s['n']})")
    else:
        st.sidebar.markdown("_(no shard reported by explain — verify in Atlas UI)_")


if st.session_state.selected_inspect:
    render_inspector(st.session_state.selected_inspect)


# Cluster-wide shard summary
with st.sidebar.expander("Cluster overview"):
    overview = shard_distribution_overview()
    st.markdown("**Customers per shard:**")
    for shard, n in sorted(overview.get("customers", {}).items()):
        st.markdown(f"- `{_zone_shard_label(shard, zone_to_shard)}`: {n}")
    st.markdown("**Orders per shard:**")
    for shard, n in sorted(overview.get("orders", {}).items()):
        st.markdown(f"- `{_zone_shard_label(shard, zone_to_shard)}`: {n:,}")
    shards = list_shards()
    if shards:
        st.markdown("**Configured shards:**")
        for s in shards:
            st.markdown(f"- `{_zone_shard_label(s.get('_id', ''), zone_to_shard)}`")


# ---------------------------------------------------------------------------
# Action banner
# ---------------------------------------------------------------------------
if st.session_state.last_action:
    st.success(st.session_state.last_action)
    st.session_state.last_action = None


# ---------------------------------------------------------------------------
# Main pane — Map
# ---------------------------------------------------------------------------
st.subheader("Customers and data residency on the world map")

if not all_customers:
    st.warning("No customers loaded. Run `python -m data.generate` first.")
    st.stop()


def build_map(customers: list[dict]) -> go.Figure:
    fig = go.Figure()

    # Lines: customer → data centre
    line_groups: dict[str, dict] = {z: {"lat": [], "lon": []} for z in ZONE_NAMES}
    for c in customers:
        z = c.get("data_zone")
        if z not in line_groups:
            continue
        dc = ZONE_REGION_INFO[z]
        line_groups[z]["lat"].extend([c["biz_lat"], dc["lat"], None])
        line_groups[z]["lon"].extend([c["biz_lon"], dc["lon"], None])

    for z in ZONE_NAMES:
        info = ZONE_REGION_INFO[z]
        if not line_groups[z]["lat"]:
            continue
        fig.add_trace(go.Scattermap(
            lat=line_groups[z]["lat"],
            lon=line_groups[z]["lon"],
            mode="lines",
            line=dict(color=info["color"], width=1),
            opacity=0.35,
            hoverinfo="skip",
            showlegend=False,
            name=f"{z} routing",
        ))

    # Customer markers — coloured by current data zone
    by_zone_x: dict[str, dict] = {z: {"lat": [], "lon": [], "text": []} for z in ZONE_NAMES}
    for c in customers:
        z = c.get("data_zone")
        if z not in by_zone_x:
            continue
        by_zone_x[z]["lat"].append(c["biz_lat"])
        by_zone_x[z]["lon"].append(c["biz_lon"])
        by_zone_x[z]["text"].append(
            f"<b>{c['_id']}</b> {c.get('name', '')}<br>"
            f"{c.get('city', '?')}, {c.get('country', '?')}<br>"
            f"Data location: <b>{c.get('location')}</b> ({z} zone)"
        )

    for z in ZONE_NAMES:
        info = ZONE_REGION_INFO[z]
        if not by_zone_x[z]["lat"]:
            continue
        fig.add_trace(go.Scattermap(
            lat=by_zone_x[z]["lat"],
            lon=by_zone_x[z]["lon"],
            mode="markers",
            marker=dict(size=8, color=info["color"]),
            text=by_zone_x[z]["text"],
            hoverinfo="text",
            name=f"{z} customers",
        ))

    # Data-centre markers — sized by tenant count
    counts = Counter(c.get("data_zone") for c in customers)
    for z in ZONE_NAMES:
        info = ZONE_REGION_INFO[z]
        n = counts.get(z, 0)
        fig.add_trace(go.Scattermap(
            lat=[info["lat"]],
            lon=[info["lon"]],
            mode="markers+text",
            marker=dict(
                size=max(20, 12 + n * 0.5),
                color=info["color"],
                opacity=0.85,
            ),
            text=[f"{z}<br>({info['city']})<br>{n}"],
            textposition="top center",
            textfont=dict(size=12, color="black"),
            hoverinfo="text",
            hovertext=[
                f"<b>{z} data centre</b><br>{info['city']} ({info['aws_region']})<br>"
                f"{n} customers · {sum(1 for _ in customers):,} total tenants"
            ],
            name=f"{z} data centre",
            showlegend=False,
        ))

    fig.update_layout(
        map=dict(style="open-street-map", center=dict(lat=20, lon=10), zoom=1.2),
        height=520,
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )
    return fig


st.plotly_chart(build_map(all_customers), use_container_width=True)


# ---------------------------------------------------------------------------
# Main pane — Customer table (data_editor)
# ---------------------------------------------------------------------------
st.subheader("Customer roster — edit the Data column to migrate")
st.caption(
    "Set a customer's **Data zone** to one of US / EU / APAC, then click "
    "**Submit migrations**. MongoDB will atomically update the `location` shard "
    "key on the customer document and all of their orders, moving them to the "
    "matching shard."
)

table_df = pd.DataFrame([
    {
        "id": c["_id"],
        "name": c["name"],
        "industry": c.get("industry", ""),
        "country": c.get("country", ""),
        "city": c.get("city", ""),
        "natural_zone": zone_for_country(c.get("country", "")),
        "location_code": c.get("location", ""),
        "data_zone": c.get("data_zone", ""),
    }
    for c in all_customers
])

edited_df = st.data_editor(
    table_df,
    use_container_width=True,
    height=420,
    column_config={
        "id": st.column_config.TextColumn("ID", width="small", disabled=True),
        "name": st.column_config.TextColumn("Customer", disabled=True),
        "industry": st.column_config.TextColumn("Industry", disabled=True),
        "country": st.column_config.TextColumn("Country", width="small", disabled=True),
        "city": st.column_config.TextColumn("City", disabled=True),
        "natural_zone": st.column_config.TextColumn(
            "Natural zone",
            help="The zone matching their business country",
            width="small",
            disabled=True,
        ),
        "location_code": st.column_config.TextColumn(
            "Loc code",
            help="The ISO country code stored in the location shard-key field",
            width="small",
            disabled=True,
        ),
        "data_zone": st.column_config.SelectboxColumn(
            "Data zone",
            help="Where MongoDB physically stores this customer's docs. Edit to migrate.",
            options=ZONE_NAMES,
            required=True,
            width="small",
        ),
    },
    hide_index=True,
    key=f"customer_table_{st.session_state.table_version}",
)

# Diff against the original frame to find changed rows
changes = []
for orig_row, new_row in zip(table_df.itertuples(index=False), edited_df.itertuples(index=False)):
    if orig_row.data_zone != new_row.data_zone:
        changes.append({"cust_id": new_row.id, "from": orig_row.data_zone, "to": new_row.data_zone})

submit_col, info_col = st.columns([1, 4])
with submit_col:
    submit_clicked = st.button(
        f"Submit migrations ({len(changes)})",
        type="primary",
        disabled=len(changes) == 0,
        use_container_width=True,
    )
with info_col:
    if changes:
        st.info(
            "Pending: "
            + ", ".join(f"`{c['cust_id']}` {c['from']}→{c['to']}" for c in changes[:6])
            + (" …" if len(changes) > 6 else "")
        )
    else:
        st.caption("No pending migrations. Edit the **Data zone** column to queue one.")

if submit_clicked and changes:
    progress = st.progress(0.0, text=f"Migrating 0 / {len(changes)} customers…")
    results = []
    for i, ch in enumerate(changes, start=1):
        result = migrate_customer(ch["cust_id"], ch["to"])
        results.append(result)
        progress.progress(i / len(changes), text=f"Migrating {i} / {len(changes)} customers…")
    progress.empty()
    total_orders = sum(r["orders_updated"] for r in results)
    total_ms = sum(r["elapsed_ms"] for r in results)
    st.session_state.last_action = (
        f"Migrated {len(results)} customer(s) and {total_orders} order(s) "
        f"in {total_ms:,.0f} ms total."
    )
    _bump_table_version()
    st.rerun()
