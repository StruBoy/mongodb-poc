"""Streamlit UI for the polymorphic citizen-services PoC.

Three views, switched from the sidebar:
  - Citizen        : the unified case timeline (every case type, one query)
  - Officer        : dashboard, cross-case Atlas Search, "add new case type"
  - Inspector      : raw documents straight from MongoDB — shows that PII is
                     genuinely ciphertext on disk, not just hidden in the UI

Run from the project root (govtech-casemgmt/):
    streamlit run app.py
"""
import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.db import get_db
from src.services import (
    add_new_case_type,
    case_summary_by_type,
    get_cases_for_citizen,
    get_citizen,
    get_raw_case,
    get_raw_citizen,
    list_citizens,
    search_cases,
    total_case_count,
)

st.set_page_config(page_title="Citizen Services Portal", layout="wide")

CASE_TYPE_EMOJI = {
    "business_permit": "🏢",
    "building_permit": "🏗️",
    "complaint": "📢",
    "benefit_application": "💰",
    "marriage_registration": "💍",
    "ev_charging_station_permit": "⚡",
}


def case_emoji(case_type: str) -> str:
    return CASE_TYPE_EMOJI.get(case_type, "📄")


def humanize(case_type: str) -> str:
    return case_type.replace("_", " ").title()


def fmt_date(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value)


def display_case_body(case: dict):
    """Strip _id / citizen_id / score and render the rest as JSON."""
    omit = {"_id", "citizen_id", "score"}
    return {k: v for k, v in case.items() if k not in omit}


# ---------------------------------------------------------------------------
# Sidebar / mode selector
# ---------------------------------------------------------------------------
st.sidebar.title("🏛️ Citizen Services")
st.sidebar.caption(
    "MongoDB Atlas: polymorphic case data, field-level encryption, unified search."
)

mode = st.sidebar.radio(
    "View as",
    ["Citizen", "Government Officer", "Encryption Inspector"],
    index=0,
)

# Total cases footer in sidebar — small architectural reminder
st.sidebar.markdown("---")
st.sidebar.caption(
    f"Total cases in **citizen_demo.cases**: {total_case_count():,}"
)
st.sidebar.caption("One collection. Five case-type schemas. One query language.")


# ===========================================================================
# CITIZEN VIEW — the polymorphism payoff
# ===========================================================================
if mode == "Citizen":
    st.title("My case history")
    st.caption(
        "One MongoDB collection holds every case type. The timeline below is "
        "a single `find({citizen_id: ...})` query — no joins, no federation."
    )

    # Find a citizen who has all five case types — the seeded demo citizen.
    db = get_db()
    pipeline = [
        {
            "$group": {
                "_id": "$citizen_id",
                "types": {"$addToSet": "$case_type"},
                "n": {"$sum": 1},
            }
        },
        {"$match": {"types": {"$size": 5}}},
        {"$sort": {"n": -1}},
        {"$limit": 8},
    ]
    multi_type_ids = [r["_id"] for r in db.cases.aggregate(pipeline)]

    # Build options: the demo citizen first, then a sample of others
    options = list(multi_type_ids)
    if len(options) < 8:
        # Pad out with random citizens so the dropdown doesn't look thin
        sample_extra = [
            c["_id"]
            for c in db.citizens.aggregate(
                [{"$sample": {"size": 8 - len(options)}}]
            )
        ]
        options.extend(i for i in sample_extra if i not in options)

    # Build labels at fetch time — citizen names are decrypted on read
    labels = {}
    for cid in options:
        c = get_citizen(cid)
        if c:
            tag = "🟢 demo" if cid in multi_type_ids[:1] else ""
            labels[cid] = f"{c['full_name']}  ·  {cid[:8]}…  {tag}"

    if not labels:
        st.error("No citizens loaded. Run `python -m data.generate` first.")
        st.stop()

    selected_id = st.selectbox(
        "Citizen account",
        options=list(labels.keys()),
        format_func=lambda cid: labels.get(cid, cid),
    )

    citizen = get_citizen(selected_id)
    cases = get_cases_for_citizen(selected_id)

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Cases on file", len(cases))
    col_b.metric("Distinct case types", len({c["case_type"] for c in cases}))
    col_c.metric("Open / in-progress", sum(
        1 for c in cases
        if c.get("status") in {
            "pending", "open", "investigating", "under_review",
            "pending_assessment", "additional_info_required",
        }
    ))

    with st.expander("👤 Citizen record (decrypted on read)"):
        st.json({
            "_id": citizen["_id"],
            "full_name": citizen["full_name"],
            "national_id": citizen["national_id"],
            "dob": citizen["dob"],
            "address": citizen["address"],
            "email": citizen["email"],
            "phone": citizen["phone"],
        })
        st.caption(
            "Notice that `national_id` and `dob` are plaintext here — the "
            "service layer decrypted them on read. Compare with the "
            "Encryption Inspector view to see how they actually live in MongoDB."
        )

    st.markdown(f"### Timeline ({len(cases)} cases)")
    if not cases:
        st.info("No cases on file.")
    else:
        for case in cases:
            ct = case["case_type"]
            with st.expander(
                f"{case_emoji(ct)}  **{humanize(ct)}**  ·  "
                f"status: `{case.get('status', '?')}`  ·  "
                f"{fmt_date(case.get('created_at'))}"
            ):
                st.json(display_case_body(case), expanded=True)


# ===========================================================================
# OFFICER VIEW — dashboard + search + add-new-type
# ===========================================================================
elif mode == "Government Officer":
    st.title("Officer dashboard")
    st.caption("All case types, one query language. No federation, no ETL.")

    # ---------- Top metrics ----------
    summary = case_summary_by_type()
    if summary:
        cols = st.columns(len(summary))
        for col, row in zip(cols, summary):
            col.metric(
                f"{case_emoji(row['_id'])} {humanize(row['_id'])}",
                f"{row['count']:,}",
                f"{row['open']:,} open",
            )

    st.markdown("---")

    # ---------- Cross-case search ----------
    st.subheader("🔍 Cross-case search")
    st.caption(
        "Atlas Search across every case type from one index. "
        "Try: `noise`, `cafe`, `extension`, `disability`, `Sydney`."
    )
    q = st.text_input("Search query", value="noise")
    if q:
        try:
            results = search_cases(q, limit=20)
        except RuntimeError as e:
            st.error(str(e))
            results = []

        if not results:
            st.info("No results.")
        else:
            type_breakdown = {}
            for r in results:
                type_breakdown[r["case_type"]] = type_breakdown.get(r["case_type"], 0) + 1
            breakdown_str = ", ".join(
                f"{case_emoji(t)} {humanize(t)}: {n}"
                for t, n in sorted(type_breakdown.items())
            )
            st.success(
                f"Found {len(results)} matches across "
                f"{len(type_breakdown)} case type(s) — {breakdown_str}"
            )

            for r in results:
                ct = r["case_type"]
                with st.container(border=True):
                    st.markdown(
                        f"{case_emoji(ct)} **{humanize(ct)}**  ·  "
                        f"status: `{r.get('status', '?')}`  ·  "
                        f"relevance: `{r.get('score', 0):.3f}`"
                    )
                    st.json(display_case_body(r), expanded=False)

    st.markdown("---")

    # ---------- Add a new case type ----------
    st.subheader("➕ Add a new case type — no migration")
    st.caption(
        "Schema-on-read: a brand-new case type is just an `insertOne` into "
        "the existing `cases` collection. Atlas Search picks up the new fields "
        "automatically thanks to the dynamic mapping."
    )

    db = get_db()
    citizen_for_new = db.citizens.find_one()
    new_type = st.text_input(
        "New case type identifier", value="ev_charging_station_permit"
    )
    default_fields = {
        "premises_address": "42 Sample St, Sydney NSW 2000",
        "charger_count": 4,
        "kw_per_charger": 50,
        "grid_capacity_check_passed": True,
        "estimated_install_cost_aud": 85000,
        "expected_completion_date": "2026-09-15",
    }
    new_fields_json = st.text_area(
        "Custom fields (JSON)", value=json.dumps(default_fields, indent=2), height=210
    )

    if st.button("Insert new case type", type="primary"):
        try:
            fields = json.loads(new_fields_json)
            new_id = add_new_case_type(new_type, citizen_for_new["_id"], fields)
            st.success(
                f"✅ Inserted `{new_type}` (_id={new_id}) into "
                f"`citizen_demo.cases`. No migration. No downtime. "
                f"Dynamic Atlas Search will index the new fields within seconds."
            )
            st.balloons()
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")
        except Exception as e:
            st.error(f"Insert failed: {e}")

    # ---------- Live case-type list ----------
    st.markdown("---")
    st.subheader("📋 Case types currently in the collection")
    fresh_summary = case_summary_by_type()
    if fresh_summary:
        df = pd.DataFrame(fresh_summary).rename(
            columns={"_id": "case_type", "count": "total", "open": "open"}
        )
        st.dataframe(df, width="stretch", hide_index=True)


# ===========================================================================
# ENCRYPTION INSPECTOR
# ===========================================================================
else:
    st.title("🔐 Encryption Inspector")
    st.caption(
        "Raw documents pulled directly from MongoDB without going through "
        "`decrypt_pii`. This is what an unauthorized reader of the database "
        "would see — even with full read access to the collection."
    )

    raw_citizen = get_raw_citizen()
    raw_benefit = get_raw_case(case_type="benefit_application")
    raw_marriage = get_raw_case(case_type="marriage_registration")

    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Citizen record (raw)")
        if raw_citizen:
            st.json(raw_citizen)
        else:
            st.info("No citizens.")

    with col_b:
        st.subheader("Benefit application (raw)")
        if raw_benefit:
            st.json(raw_benefit)
        else:
            st.info("No benefit_application docs.")

    st.markdown("---")
    st.subheader("Marriage registration (raw)")
    if raw_marriage:
        st.json(raw_marriage)

    st.markdown("---")
    st.info(
        "Notice that `national_id`, `dob`, `tax_file_number`, and "
        "`partner_national_id` show up as `_encrypted` envelopes — base64 "
        "ciphertext + a per-record nonce. The encryption key never leaves "
        "the application; the database stores ciphertext only. Compare "
        "with the Citizen view to see the same fields decrypted on read."
    )
