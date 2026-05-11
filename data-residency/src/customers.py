"""Customer CRUD helpers.

The `customers` collection is sharded on `{location: 1, _id: 1}`. Each
customer document has:
    _id            cust-001 .. cust-100
    name           string (company name)
    industry       string
    country        ISO 3166-1 alpha-2 of the customer's business address
    city           string
    biz_lat        float
    biz_lon        float
    location       ISO 3166-1 alpha-2 — the shard-key zone code. One of the
                   three representative codes: "US", "DE", or "SG"
    created_at     datetime
"""
from __future__ import annotations

from src.db import get_db
from src.zones import zone_for_country


def list_customers() -> list[dict]:
    """Return all customer docs. `location` is the raw ISO country code on
    disk; `data_zone` is the resolved zone name (US/EU/APAC) for UI display."""
    docs = list(get_db().customers.find().sort("_id", 1))
    for d in docs:
        d["data_zone"] = zone_for_country(d.get("location", ""))
    return docs


def get_customer(cust_id: str) -> dict | None:
    d = get_db().customers.find_one({"_id": cust_id})
    if d:
        d["data_zone"] = zone_for_country(d.get("location", ""))
    return d


def customer_count_by_location() -> dict[str, int]:
    """Per-zone customer counts. Keys are zone names (US/EU/APAC), summed
    across all country codes that map to each zone."""
    pipeline = [{"$group": {"_id": "$location", "n": {"$sum": 1}}}]
    by_zone: dict[str, int] = {}
    for row in get_db().customers.aggregate(pipeline):
        zone = zone_for_country(row["_id"])
        by_zone[zone] = by_zone.get(zone, 0) + row["n"]
    return by_zone
