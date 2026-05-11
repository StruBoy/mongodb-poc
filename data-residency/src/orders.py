"""Order CRUD helpers.

The `orders` collection is sharded on `{location: 1, customer_id: 1}` so the
zone-discriminator and the customer-grouping field together form the shard
key. This guarantees a customer's orders all live in the same chunk as their
customer document, so a single zone update migrates everything together.

Each order document:
    _id            ObjectId (default)
    customer_id    cust-001 ..
    location       must equal the parent customer.location
    sku            string
    qty            int
    unit_price_usd float
    total_usd      float
    placed_at      datetime
"""
from __future__ import annotations

from src.db import get_db
from src.zones import zone_for_country


def orders_for_customer(cust_id: str, limit: int | None = None) -> list[dict]:
    cursor = get_db().orders.find({"customer_id": cust_id}).sort("placed_at", -1)
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)


def order_count_for_customer(cust_id: str) -> int:
    return get_db().orders.count_documents({"customer_id": cust_id})


def order_count_by_location() -> dict[str, int]:
    """Per-zone order counts. Keys are zone names (US/EU/APAC), summed across
    all country codes that map to each zone."""
    pipeline = [{"$group": {"_id": "$location", "n": {"$sum": 1}}}]
    by_zone: dict[str, int] = {}
    for row in get_db().orders.aggregate(pipeline):
        zone = zone_for_country(row["_id"])
        by_zone[zone] = by_zone.get(zone, 0) + row["n"]
    return by_zone
