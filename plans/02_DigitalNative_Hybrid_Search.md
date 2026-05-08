# PoC 2: Unified Product Catalog with Hybrid Search — Implementation Plan

**Customer Segment:** Digital-Native Tech (e-commerce, marketplaces, content platforms)
**Time Budget:** 3.5 hours
**Cluster Tier:** Atlas M10 (recommended for index performance)
**Total Cost (afternoon):** ~USD 3

---

## What you're building

A product catalog service where a single MongoDB Atlas cluster powers three search modes side by side: keyword search (Atlas Search), semantic search (Vector Search), and hybrid (rank fusion). A Streamlit shopping interface lets the audience compare results across modes for the same query.

## What it demonstrates

- **One platform, three workloads**: filter, full-text, semantic
- **Hybrid search via `$rankFusion`**: combines lexical and vector results in a single query
- **Sub-100ms response** across all modes on a realistic catalog
- **Architectural simplification**: replaces a typical Postgres + Elasticsearch + Pinecone stack

## The demo moment

The user types "shoes for running long distances." Filters by price under AUD 250. Within 100ms, three result panes show:
- Keyword: misses, because the product descriptions don't contain "long distances"
- Semantic: nails it, returning marathon and trail-running shoes
- Hybrid: combines both, ranked sensibly

The audience instantly grasps why hybrid wins, and why doing this on three separate systems would be a nightmare.

---

## Prerequisites

- Atlas account with cluster permissions
- Voyage AI API key
- Anthropic API key (used to generate realistic product descriptions)
- Python 3.11+

---

## Phase 1: Atlas Setup (30 min)

### 1.1 Provision the cluster

1. Create project `poc-hybrid-search`
2. Build cluster: M10, AWS, Singapore region
3. Wait ~7 minutes for provisioning

### 1.2 Configure access

- Database user `pocuser` with `readWriteAnyDatabase` role
- Network access from your IP

### 1.3 Create the database

```javascript
use catalog_demo
db.createCollection("products")
```

### 1.4 Define both search indexes

**Atlas Search index** on `catalog_demo.products`, name `products_text_idx`:

```json
{
  "mappings": {
    "dynamic": false,
    "fields": {
      "title": { "type": "string", "analyzer": "lucene.standard" },
      "description": { "type": "string", "analyzer": "lucene.standard" },
      "brand": { "type": "string", "analyzer": "lucene.keyword" },
      "category": { "type": "token" },
      "price": { "type": "number" }
    }
  }
}
```

**Vector Search index** on the same collection, name `products_vector_idx`:

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
      "path": "category"
    },
    {
      "type": "filter",
      "path": "price"
    }
  ]
}
```

Both indexes will populate after data load.

---

## Phase 2: Project Setup & Catalog Generation (60 min)

### 2.1 Project scaffolding

```bash
mkdir poc-hybrid-search && cd poc-hybrid-search
python -m venv venv && source venv/bin/activate
pip install pymongo voyageai anthropic streamlit python-dotenv pandas
```

`.env`:

```
MONGODB_URI=mongodb+srv://pocuser:<password>@<cluster>.mongodb.net/
VOYAGE_API_KEY=pa-xxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxx
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
    return get_client()["catalog_demo"]
```

### 2.3 Embedding helper (`src/embed.py`)

```python
import os
import voyageai
from dotenv import load_dotenv

load_dotenv()
vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])

def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    result = vo.embed(texts, model="voyage-3", input_type=input_type)
    return result.embeddings

def product_to_text(p: dict) -> str:
    return f"{p['title']}. {p['description']} Brand: {p['brand']}. Category: {p['category']}."
```

### 2.4 Catalog generator (`data/generate.py`)

The trick to a compelling demo is realistic descriptions. We use Claude to generate them.

```python
import os
import random
import json
from anthropic import Anthropic
from faker import Faker
from src.db import get_db
from src.embed import embed_texts, product_to_text
from dotenv import load_dotenv

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
fake = Faker()
random.seed(42)

CATEGORIES = {
    "footwear": [
        ("trail running shoe", 80, 280),
        ("marathon racing shoe", 150, 350),
        ("casual sneaker", 60, 180),
        ("hiking boot", 120, 350),
        ("dress shoe", 100, 400),
    ],
    "electronics": [
        ("noise-cancelling headphones", 100, 600),
        ("wireless earbuds", 50, 400),
        ("smart watch", 150, 800),
        ("portable speaker", 40, 300),
        ("e-reader", 120, 400),
    ],
    "home": [
        ("espresso machine", 200, 1500),
        ("air purifier", 100, 800),
        ("kitchen knife set", 80, 500),
        ("memory foam pillow", 30, 150),
        ("standing desk", 250, 1200),
    ],
    "sports": [
        ("yoga mat", 25, 150),
        ("resistance band set", 20, 80),
        ("road bike helmet", 60, 350),
        ("fitness tracker", 80, 400),
        ("compression tights", 40, 180),
    ],
}

BRANDS = {
    "footwear": ["TrailMaster", "PaceForge", "StridePro", "TerraVibe", "RunCadence"],
    "electronics": ["SoundCrest", "NovaWave", "AudioPath", "Lumiq", "EchoForm"],
    "home": ["KitchenAxis", "HomeNest", "PureLine", "VeritasGoods", "Quietude"],
    "sports": ["FlexCore", "AthleteOne", "VeloPath", "PulseGear", "MotionWright"],
}


def generate_description_batch(items: list[tuple]) -> list[str]:
    """Use Claude to generate realistic product descriptions in one batch call."""
    prompt = "Generate a realistic product description (40-60 words) for each of these products. " \
             "Return as a JSON array of strings, one description per product, in the same order. " \
             "Descriptions should be evocative but not use the exact product type name. " \
             "Return ONLY the JSON array, no other text.\n\n"
    for i, (title, brand, category) in enumerate(items, 1):
        prompt += f"{i}. {brand} {title} ({category})\n"

    response = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_catalog(target_count: int = 5000):
    items = []
    for category, products in CATEGORIES.items():
        per_subtype = target_count // (len(CATEGORIES) * len(products))
        for product_type, min_price, max_price in products:
            for _ in range(per_subtype):
                brand = random.choice(BRANDS[category])
                model = fake.bothify(text="??-####").upper()
                items.append({
                    "title": f"{brand} {model}",
                    "_product_type": product_type,
                    "brand": brand,
                    "category": category,
                    "price": round(random.uniform(min_price, max_price), 2),
                    "rating": round(random.uniform(3.5, 5.0), 1),
                    "review_count": random.randint(5, 2000),
                })
    return items


def main():
    db = get_db()
    db.products.delete_many({})

    print("Generating catalog skeleton...")
    items = generate_catalog(target_count=2500)  # 2500 keeps Voyage costs under USD 0.50
    print(f"Generated {len(items)} product records.")

    # Generate descriptions in batches of 25
    print("Generating descriptions via Claude...")
    for i in range(0, len(items), 25):
        batch = items[i:i+25]
        tuples = [(it["_product_type"], it["brand"], it["category"]) for it in batch]
        try:
            descriptions = generate_description_batch(tuples)
            for it, desc in zip(batch, descriptions):
                it["description"] = desc
                del it["_product_type"]
        except Exception as e:
            print(f"Batch {i} failed: {e}; using fallback.")
            for it in batch:
                it["description"] = f"Premium {it['_product_type']} from {it['brand']}."
                del it["_product_type"]
        if i % 100 == 0:
            print(f"  ...{i}/{len(items)}")

    # Embed descriptions
    print("Embedding descriptions via Voyage AI...")
    for i in range(0, len(items), 50):
        batch = items[i:i+50]
        texts = [product_to_text(it) for it in batch]
        embeddings = embed_texts(texts, input_type="document")
        for it, emb in zip(batch, embeddings):
            it["embedding"] = emb

    db.products.insert_many(items)
    print(f"Inserted {len(items)} products.")


if __name__ == "__main__":
    main()
```

Run it:

```bash
python -m data.generate
```

This takes ~10 minutes (description generation is the slow step). Cost: ~USD 0.30 in Claude API + ~USD 0.10 in Voyage embeddings.

After insert, wait ~1 minute for both Atlas Search and Vector Search indexes to build. Verify in Atlas UI.

---

## Phase 3: Search Modes (45 min)

### 3.1 Search functions (`src/core.py`)

```python
from src.db import get_db
from src.embed import embed_texts


def keyword_search(query: str, category: str = None, max_price: float = None, limit: int = 10):
    db = get_db()
    must_clauses = [{"text": {"query": query, "path": ["title", "description", "brand"]}}]
    filter_clauses = []
    if category:
        filter_clauses.append({"equals": {"path": "category", "value": category}})
    if max_price:
        filter_clauses.append({"range": {"path": "price", "lte": max_price}})

    pipeline = [
        {
            "$search": {
                "index": "products_text_idx",
                "compound": {
                    "must": must_clauses,
                    "filter": filter_clauses
                }
            }
        },
        {"$limit": limit},
        {"$project": {
            "embedding": 0,
            "score": {"$meta": "searchScore"}
        }}
    ]
    return list(db.products.aggregate(pipeline))


def semantic_search(query: str, category: str = None, max_price: float = None, limit: int = 10):
    db = get_db()
    [embedding] = embed_texts([query], input_type="query")

    filter_doc = {}
    if category:
        filter_doc["category"] = category
    if max_price:
        filter_doc["price"] = {"$lte": max_price}

    vector_stage = {
        "$vectorSearch": {
            "index": "products_vector_idx",
            "path": "embedding",
            "queryVector": embedding,
            "numCandidates": 200,
            "limit": limit
        }
    }
    if filter_doc:
        vector_stage["$vectorSearch"]["filter"] = filter_doc

    pipeline = [
        vector_stage,
        {"$project": {
            "embedding": 0,
            "score": {"$meta": "vectorSearchScore"}
        }}
    ]
    return list(db.products.aggregate(pipeline))


def hybrid_search(query: str, category: str = None, max_price: float = None, limit: int = 10):
    """Hybrid using $rankFusion (reciprocal rank fusion) — combines text and vector scoring."""
    db = get_db()
    [embedding] = embed_texts([query], input_type="query")

    text_pipeline = [
        {"$search": {
            "index": "products_text_idx",
            "compound": {
                "must": [{"text": {"query": query, "path": ["title", "description", "brand"]}}],
                "filter": _build_search_filter(category, max_price)
            }
        }},
        {"$limit": 50}
    ]

    vector_pipeline = [
        {"$vectorSearch": {
            "index": "products_vector_idx",
            "path": "embedding",
            "queryVector": embedding,
            "numCandidates": 200,
            "limit": 50,
            **({"filter": _build_vector_filter(category, max_price)} if (category or max_price) else {})
        }}
    ]

    pipeline = [
        {
            "$rankFusion": {
                "input": {
                    "pipelines": {
                        "textPipeline": text_pipeline,
                        "vectorPipeline": vector_pipeline
                    }
                }
            }
        },
        {"$limit": limit},
        {"$project": {
            "embedding": 0,
            "score": {"$meta": "scoreDetails"}
        }}
    ]
    return list(db.products.aggregate(pipeline))


def _build_search_filter(category, max_price):
    f = []
    if category:
        f.append({"equals": {"path": "category", "value": category}})
    if max_price:
        f.append({"range": {"path": "price", "lte": max_price}})
    return f


def _build_vector_filter(category, max_price):
    f = {}
    if category:
        f["category"] = category
    if max_price:
        f["price"] = {"$lte": max_price}
    return f
```

### 3.2 Quick smoke test

```python
# scripts/test_search.py
from src.core import keyword_search, semantic_search, hybrid_search

q = "shoes for running long distances"
print("=== Keyword ===")
for p in keyword_search(q, category="footwear", limit=5):
    print(f"  {p['title']} ({p['brand']}) — {p['price']:.2f}")

print("=== Semantic ===")
for p in semantic_search(q, category="footwear", limit=5):
    print(f"  {p['title']} ({p['brand']}) — {p['price']:.2f}")

print("=== Hybrid ===")
for p in hybrid_search(q, category="footwear", limit=5):
    print(f"  {p['title']} ({p['brand']}) — {p['price']:.2f}")
```

Run it. Semantic should clearly outperform keyword on this query.

---

## Phase 4: Streamlit UI (60 min)

### 4.1 The shopping interface (`app.py`)

```python
import streamlit as st
from src.core import keyword_search, semantic_search, hybrid_search

st.set_page_config(page_title="Hybrid Search Demo", layout="wide")
st.title("🛍️ Unified Product Catalog — One Database, Three Search Modes")
st.caption("MongoDB Atlas: Atlas Search + Vector Search in a single cluster")

# Search controls
col1, col2, col3 = st.columns([4, 2, 2])
query = col1.text_input("Search", value="shoes for running long distances")
category = col2.selectbox("Category", ["", "footwear", "electronics", "home", "sports"])
max_price = col3.number_input("Max price (AUD)", min_value=0, max_value=2000, value=300, step=50)

if not query:
    st.stop()

cat_filter = category if category else None
price_filter = max_price if max_price > 0 else None

# Run all three searches
with st.spinner("Running keyword, semantic, and hybrid searches..."):
    kw_results = keyword_search(query, cat_filter, price_filter, limit=5)
    sem_results = semantic_search(query, cat_filter, price_filter, limit=5)
    hyb_results = hybrid_search(query, cat_filter, price_filter, limit=5)


def render_results(results, mode_label):
    if not results:
        st.info(f"No {mode_label} matches.")
        return
    for r in results:
        with st.container(border=True):
            st.markdown(f"**{r['title']}** — *{r['brand']}*")
            st.caption(r.get("description", ""))
            cols = st.columns([1, 1, 2])
            cols[0].metric("Price", f"AUD {r['price']:.2f}")
            cols[1].metric("Rating", f"⭐ {r['rating']}")
            if "score" in r and isinstance(r["score"], (int, float)):
                cols[2].metric("Relevance", f"{r['score']:.3f}")


col_kw, col_sem, col_hyb = st.columns(3)

with col_kw:
    st.subheader("🔤 Keyword")
    st.caption("Atlas Search — exact and lexical matches")
    render_results(kw_results, "keyword")

with col_sem:
    st.subheader("🧠 Semantic")
    st.caption("Vector Search — meaning-based matches")
    render_results(sem_results, "semantic")

with col_hyb:
    st.subheader("⚡ Hybrid")
    st.caption("$rankFusion — combined ranking")
    render_results(hyb_results, "hybrid")
```

### 4.2 Launch

```bash
streamlit run app.py
```

---

## Phase 5: Demo Polish (30 min)

Curate three demo queries that visibly differentiate the modes. For the differentiation to land cleanly the dataset has to be tuned to match — see notes after each query.

1. **`"racing 26.2 miles"`** + footwear — semantic dominates because the description prompt explicitly bans intent vocabulary (`marathon`, `race`, `mile`, `endurance`, `long-distance`, …) for footwear products. Keyword has no lexical anchor and returns near-empty / random results. Semantic embeds the concept and surfaces marathon racing + trail running shoes.
2. **`"TrailMaster"`** + footwear — keyword dominates because the brand field is exact-matched by Atlas Search's `lucene.keyword` analyzer. Semantic loses because `product_to_text` embeds only `description + category` — the brand never enters the vector space, so semantic drifts to other trail-themed shoes regardless of brand.
3. **`"comfortable for long flights"`** + electronics — both modes contribute. Electronics descriptions are *not* banned-word constrained, so keyword catches `comfortable`, `long`, `flights`, `travel`. Semantic catches the noise-cancelling-headphones intent. Hybrid blends them.

Save these as quick-fill buttons in the sidebar:

```python
# Add to sidebar
st.sidebar.subheader("Try these queries")
demo_queries = [
    ("Marathon racing", "racing 26.2 miles", "footwear", 400),
    ("Brand search",    "TrailMaster",                    "footwear", 500),
    ("Long flights",    "comfortable for long flights",   "electronics", 800),
]
for label, q, c, p in demo_queries:
    if st.sidebar.button(label):
        st.session_state.query = q
        st.session_state.category = c
        st.session_state.max_price = p
        st.rerun()
```

---

## Demo Script

**[1 min] Set up the architectural problem.**
"If you run a digital product business, you almost certainly run three data systems for product search: an operational database for the catalog, a search service like Elasticsearch for full-text, and increasingly a vector database like Pinecone for AI-powered semantic search. Three systems, three sets of indexes, three places where data can drift out of sync."

**[1 min] Show the catalog.**
"This is one MongoDB cluster with about 2,500 products. There's no separate search service. There's no separate vector database. The same documents that hold the operational data also hold the embeddings for semantic search."

**[1 min] Run the marathon query.**
Click "Marathon racing" — the query is `"racing 26.2 miles"`. "Watch the three panes update simultaneously. The keyword search has nothing to anchor on: the descriptions in this catalog don't contain words like `marathon`, `race`, `mile`, or `endurance` — those are exactly the intent terms a buyer would type. The semantic pane has no such handicap; it embeds the concept and surfaces actual marathon racing and trail running shoes. The hybrid pane carries the semantic ranking through."

**[30 sec] Run the brand query.**
Click "Brand search" — `"TrailMaster"`. "Now keyword wins decisively. Atlas Search exact-matches the brand field, so the keyword pane is 100% TrailMaster products. The semantic pane drifts to other brands' trail-themed shoes because the brand string was never embedded — vector search has no idea what `TrailMaster` is. Hybrid leans on the keyword pipeline and prioritises the exact-brand hits."

**[30 sec] Land the architectural point.**
"This is one cluster. One query language. One set of indexes to maintain. The reason this matters isn't that semantic search is faster — it's that the team building this product gets to focus on the product instead of on synchronizing three systems."

---

## Troubleshooting

**`$rankFusion` returns an error.**
Requires MongoDB 8.1+. Atlas should be on this version on M10+; verify under cluster details. If unavailable, fall back to a manual reciprocal rank fusion in application code.

**Semantic search returns the same results regardless of query.**
Embeddings used `input_type="document"` for queries. Confirm `embed_texts(..., input_type="query")` in the search functions.

**Catalog generation hits Anthropic rate limits.**
Add `time.sleep(1)` between batches. For aggressive parallelism, use `claude-haiku-4-5` (cheaper and higher rate limits than Opus).

**Atlas Search index returns no results for valid queries.**
Confirm the index status is "Active" in Atlas UI. The `lucene.standard` analyzer is required for natural-language queries; `lucene.keyword` only works for exact match.

---

## Cleanup

```bash
# Pause or terminate the Atlas cluster
# In your shell:
deactivate
rm -rf poc-hybrid-search
```

---

## Variations

- **Add personalization**: store user click history, embed it, blend user-vector into the query vector for re-ranking
- **Add image search**: use a multimodal embedding model (e.g. Voyage's multimodal model) and let users upload an image to find similar products
- **A/B test mode**: route 50% of synthetic "users" to keyword, 50% to hybrid, and show conversion-rate-style metrics to make a commercial argument

## Demo trade-offs to flag if asked

- The footwear description prompt deliberately bans intent vocabulary (`marathon`, `race`, `mile`, `endurance`, `long-distance`, …) to make the keyword-fail demo land. In a real catalog you would *want* those words present — the constraint exists only to expose the differentiation between modes on a small corpus. Other categories are unconstrained.
- The Claude prompt does NOT receive the brand name, only `(product_type, category)`. Without that, descriptions tinted themselves toward the brand (e.g., `TrailMaster trail running shoe` → unusually trail-emphatic descriptions) and a small number leaked the brand string verbatim, both of which let semantic search resolve brand-only queries. After the change, descriptions are brand-agnostic and a regex pass strips any accidental brand mention before insert.
- `product_to_text` embeds only `description + category` — the brand string is intentionally excluded so semantic search loses brand-only queries. In production you would normally include the brand in the embedded text so the vector pipeline can also resolve brand-affiliated queries; the demo strips it to make the keyword-wins case unambiguous.
