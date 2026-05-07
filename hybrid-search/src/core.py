"""Search functions for the hybrid-search PoC.

Three modes against the same `products` collection:
  - keyword_search:  Atlas Search lexical match
  - semantic_search: Vector Search by query embedding
  - hybrid_search:   native $rankFusion of the two (MongoDB 8.1+)
"""
from src.db import get_db
from src.embed import embed_texts


def keyword_search(query: str, category: str = None, max_price: float = None, limit: int = 10):
    db = get_db()
    pipeline = [
        {"$search": {
            "index": "products_text_idx",
            "compound": {
                "must": [{"text": {"query": query, "path": ["title", "description", "brand"]}}],
                "filter": _build_search_filter(category, max_price),
            },
        }},
        {"$limit": limit},
        {"$project": {
            "embedding": 0,
            "score": {"$meta": "searchScore"},
        }},
    ]
    return list(db.products.aggregate(pipeline))


def semantic_search(query: str, category: str = None, max_price: float = None, limit: int = 10):
    db = get_db()
    [embedding] = embed_texts([query], input_type="query")

    vector_stage = {
        "$vectorSearch": {
            "index": "products_vector_idx",
            "path": "embedding",
            "queryVector": embedding,
            "numCandidates": 200,
            "limit": limit,
        }
    }
    filter_doc = _build_vector_filter(category, max_price)
    if filter_doc:
        vector_stage["$vectorSearch"]["filter"] = filter_doc

    pipeline = [
        vector_stage,
        {"$project": {
            "embedding": 0,
            "score": {"$meta": "vectorSearchScore"},
        }},
    ]
    return list(db.products.aggregate(pipeline))


def hybrid_search(query: str, category: str = None, max_price: float = None, limit: int = 10):
    """Native $rankFusion of keyword and semantic results. Requires MongoDB 8.1+."""
    db = get_db()
    [embedding] = embed_texts([query], input_type="query")

    text_pipeline = [
        {"$search": {
            "index": "products_text_idx",
            "compound": {
                "must": [{"text": {"query": query, "path": ["title", "description", "brand"]}}],
                "filter": _build_search_filter(category, max_price),
            },
        }},
        {"$limit": 50},
    ]

    vector_stage = {
        "$vectorSearch": {
            "index": "products_vector_idx",
            "path": "embedding",
            "queryVector": embedding,
            "numCandidates": 200,
            "limit": 50,
        }
    }
    filter_doc = _build_vector_filter(category, max_price)
    if filter_doc:
        vector_stage["$vectorSearch"]["filter"] = filter_doc
    vector_pipeline = [vector_stage]

    pipeline = [
        {"$rankFusion": {
            "input": {
                "pipelines": {
                    "textPipeline": text_pipeline,
                    "vectorPipeline": vector_pipeline,
                }
            },
            "scoreDetails": True,
        }},
        {"$addFields": {"score": {"$meta": "score"}}},
        {"$limit": limit},
        {"$project": {"embedding": 0}},
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
