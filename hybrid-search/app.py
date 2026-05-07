"""Streamlit UI for the hybrid-search PoC.

Two views via tabs:
  - Search compare: 3-pane keyword/semantic/hybrid comparison
  - Full catalog: sortable, filterable view of every product in the collection

Run from the project root:
    streamlit run app.py
"""
import pandas as pd
import streamlit as st

from src.core import hybrid_search, keyword_search, semantic_search
from src.db import get_db

CATEGORIES = ["footwear", "electronics", "home", "sports"]

st.set_page_config(page_title="Hybrid Search Demo", layout="wide")
st.title("🛍️ Unified Product Catalog — One Database, Three Search Modes")
st.caption("MongoDB Atlas: Atlas Search + Vector Search in a single cluster")

# ----------------------------------------------------------------------------
# Sidebar: pre-baked demo queries (relevant to the Search tab)
# ----------------------------------------------------------------------------
DEMO_QUERIES = [
    ("Marathon training", "shoes for running long distances", "footwear", 400),
    ("Brand search",       "TrailMaster",                    "footwear", 500),
    ("Long flights",       "comfortable for long flights",   "electronics", 800),
]

st.sidebar.header("Demo queries")
st.sidebar.caption("Loads into the Search compare tab.")
for label, q, cat, price in DEMO_QUERIES:
    if st.sidebar.button(label, use_container_width=True):
        st.session_state["query"] = q
        st.session_state["category"] = cat
        st.session_state["max_price"] = price
        st.rerun()


@st.cache_data(ttl=300, show_spinner="Loading catalog...")
def load_catalog() -> pd.DataFrame:
    """Pull every product (excluding the embedding) into a DataFrame. Cached for 5 minutes."""
    docs = list(get_db().products.find({}, {"embedding": 0}))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["_id"] = df["_id"].astype(str)
    return df


tab_search, tab_catalog = st.tabs(["🔍 Search compare", "📋 Full catalog"])

# ============================================================================
# TAB 1: Search compare
# ============================================================================
with tab_search:
    col_q, col_cat, col_price = st.columns([4, 2, 2])
    query = col_q.text_input(
        "Search",
        value=st.session_state.get("query", "shoes for running long distances"),
        key="query",
    )
    cat_options = [""] + CATEGORIES
    category = col_cat.selectbox(
        "Category",
        cat_options,
        index=cat_options.index(st.session_state.get("category", "footwear")),
        key="category",
    )
    max_price = col_price.number_input(
        "Max price (AUD)",
        min_value=0,
        max_value=2000,
        value=st.session_state.get("max_price", 400),
        step=50,
        key="max_price",
    )

    if not query:
        st.info("Enter a query above or pick a demo query in the sidebar.")
    else:
        cat_filter = category if category else None
        price_filter = max_price if max_price > 0 else None

        with st.spinner("Running keyword, semantic, and hybrid searches..."):
            kw_results = keyword_search(query, cat_filter, price_filter, limit=5)
            sem_results = semantic_search(query, cat_filter, price_filter, limit=5)
            hyb_results = hybrid_search(query, cat_filter, price_filter, limit=5)

        def render_results(results: list, mode_label: str):
            if not results:
                st.info(f"No {mode_label} matches.")
                return
            for r in results:
                with st.container(border=True):
                    st.markdown(f"**{r['title']}** — *{r['brand']}*")
                    st.caption(r.get("description", ""))
                    score = r.get("score")
                    score_str = f"{score:.4f}" if isinstance(score, (int, float)) else "—"
                    st.markdown(
                        f"💲 **${r['price']:.2f}** &nbsp;·&nbsp; "
                        f"⭐ {r.get('rating', 0)} &nbsp;·&nbsp; "
                        f"score `{score_str}`"
                    )

        col_kw, col_sem, col_hyb = st.columns(3)
        with col_kw:
            st.subheader("🔤 Keyword")
            st.caption("Atlas Search — lexical / BM25 match on title, description, brand")
            render_results(kw_results, "keyword")
        with col_sem:
            st.subheader("🧠 Semantic")
            st.caption("Vector Search — cosine similarity on voyage-3 embeddings")
            render_results(sem_results, "semantic")
        with col_hyb:
            st.subheader("⚡ Hybrid")
            st.caption("$rankFusion — reciprocal rank fusion of both pipelines")
            render_results(hyb_results, "hybrid")

# ============================================================================
# TAB 2: Full catalog
# ============================================================================
with tab_catalog:
    df = load_catalog()
    if df.empty:
        st.warning("Catalog is empty. Run `python -m data.generate` first.")
        st.stop()

    f_col1, f_col2, f_col3 = st.columns([2, 2, 4])
    cat_filter = f_col1.selectbox("Category", ["All"] + CATEGORIES, key="cat_view_category")
    brands_in_data = sorted(df["brand"].dropna().unique().tolist())
    brand_filter = f_col2.multiselect("Brand", brands_in_data, key="cat_view_brands")
    text_filter = f_col3.text_input(
        "Filter title / description (substring, case-insensitive)",
        key="cat_view_text",
    )

    p_col1, p_col2 = st.columns(2)
    price_lo, price_hi = float(df["price"].min()), float(df["price"].max())
    price_range = p_col1.slider(
        "Price range (AUD)",
        min_value=0.0,
        max_value=float(round(price_hi + 1)),
        value=(0.0, float(round(price_hi + 1))),
        step=10.0,
        key="cat_view_price",
    )
    rating_min = p_col2.slider("Minimum rating", 0.0, 5.0, 0.0, 0.1, key="cat_view_rating")

    view = df.copy()
    if cat_filter != "All":
        view = view[view["category"] == cat_filter]
    if brand_filter:
        view = view[view["brand"].isin(brand_filter)]
    if text_filter:
        needle = text_filter.lower()
        view = view[
            view["title"].str.lower().str.contains(needle, na=False)
            | view["description"].str.lower().str.contains(needle, na=False)
        ]
    view = view[(view["price"] >= price_range[0]) & (view["price"] <= price_range[1])]
    view = view[view["rating"] >= rating_min]

    st.caption(f"Showing **{len(view):,}** of **{len(df):,}** products")

    column_order = ["title", "brand", "category", "price", "rating", "review_count", "description", "_id"]
    view = view[column_order]
    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "title":        st.column_config.TextColumn("Title",       width="medium"),
            "brand":        st.column_config.TextColumn("Brand",       width="small"),
            "category":     st.column_config.TextColumn("Category",    width="small"),
            "price":        st.column_config.NumberColumn("Price",     format="$%.2f", width="small"),
            "rating":       st.column_config.NumberColumn("Rating",    format="%.1f ⭐", width="small"),
            "review_count": st.column_config.NumberColumn("Reviews",   format="%d", width="small"),
            "description":  st.column_config.TextColumn("Description", width="large"),
            "_id":          st.column_config.TextColumn("Mongo _id",   width="medium"),
        },
    )
