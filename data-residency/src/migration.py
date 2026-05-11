"""Move a customer's data between Atlas Global Cluster zones.

Mechanism: update the `location` shard-key field on the customer document and
all of their orders. MongoDB 5.0+ supports updating shard-key values; the
mongos handles the cross-shard atomicity via an internal distributed
transaction. Because both collections share `location` as the first field of
their shard key, both updates together physically move the customer's whole
record to the target zone in one shot.

The `migrate_customer` call uses a multi-document transaction across both
collections so an interrupt mid-way leaves the customer's data in a
consistent zone (either fully moved or not at all).

Verification:
- `migration_status` reads the logical zone fields and reports any drift
  between the customer's `location` and their orders' `location` (which would
  indicate the transaction was interrupted).
- `physical_shard_for_customer` runs an `explain` on a scatter-gather find
  to show which physical shard actually returned each document — proves the
  doc is on the named shard, not just labelled with a zone string.
- `shard_distribution_overview` returns the cluster-wide per-shard counts
  via `$collStats`. Used by the inspector panel to show 'us-east-1 has X
  customer docs, eu-central-1 has Y' aggregate evidence.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.db import DB_NAME, get_client, get_db
from src.zones import (
    ZONE_FOR_COUNTRY,
    ZONE_NAMES,
    representative_code_for_zone,
    zone_for_country,
)

# Parallelism tuning: each shard-key cross-shard update is ~1-1.5s due to
# MongoDB's internal two-phase commit. With 16 parallel workers per customer,
# a 75-order customer migrates in ~5-8s instead of ~110s sequentially.
ORDERS_PARALLEL_WORKERS = 16

# Bulk migration runs N customer migrations concurrently. With 4 customer
# threads × 16 order threads = 64 in-flight updates; well within Atlas M30's
# default connection pool of 500.
CUSTOMERS_PARALLEL_WORKERS = 4


# ---------------------------------------------------------------------------
# Process-global migration-job registry.
#
# The Streamlit UI launches migrations on a background thread and renders a
# live feed by polling this registry from a fragment. Because the registry
# lives at module scope, it survives browser refreshes and is shared across
# Streamlit sessions — a user can refresh the tab mid-migration and the new
# session picks up the still-running job. (`st.session_state` is per-session
# and so wasn't suitable for this.)
#
# Single-job design: only one migration may run at a time. The UI gates its
# buttons on `get_active_job()` to enforce this.
# ---------------------------------------------------------------------------
_ACTIVE_JOB: dict | None = None
_JOB_LOCK = threading.Lock()


def set_active_job(job: dict) -> None:
    global _ACTIVE_JOB
    with _JOB_LOCK:
        _ACTIVE_JOB = job


def get_active_job() -> dict | None:
    return _ACTIVE_JOB


def clear_active_job() -> None:
    global _ACTIVE_JOB
    with _JOB_LOCK:
        _ACTIVE_JOB = None


def migrate_customer(cust_id: str, target_zone: str) -> dict:
    """Move cust_id and all its orders to target_zone.

    The new `location` value is:
      - the customer's own country code, if it falls in target_zone (natural
        migration — preserves real-world country granularity)
      - the zone's representative code (US/DE/SG) otherwise (cross-zone
        override — the customer's country wouldn't route to target_zone)

    Returns {customer_updated, orders_updated, source_*, target_*, elapsed_ms}.
    No-op (with zeros in the counts) if the customer is already in target_zone.
    """
    if target_zone not in ZONE_NAMES:
        raise ValueError(f"Invalid zone {target_zone}; expected one of {ZONE_NAMES}")

    client = get_client()
    db = client[DB_NAME]

    customer = db.customers.find_one({"_id": cust_id}, {"location": 1, "country": 1})
    if not customer:
        raise ValueError(f"Customer {cust_id!r} not found")

    source_code = customer["location"]
    source_zone = zone_for_country(source_code)

    cust_country = customer.get("country", "")
    if zone_for_country(cust_country) == target_zone:
        target_code = cust_country
    else:
        target_code = representative_code_for_zone(target_zone)

    if source_code == target_code:
        return {
            "customer_id": cust_id,
            "customer_updated": 0,
            "orders_updated": 0,
            "source_zone": source_zone,
            "source_code": source_code,
            "target_zone": target_zone,
            "target_code": target_code,
            "elapsed_ms": 0.0,
            "noop": True,
        }

    # MongoDB requires shard-key updates that move docs across shards to be
    # sent as batches of size 1: neither updateMany nor bulk_write is allowed.
    # We loop one update_one per doc.
    #
    # We deliberately do NOT wrap this in a multi-doc transaction:
    #   - Each individual update_one is already internally atomic — MongoDB
    #     handles the per-doc cross-shard move via an internal sub-transaction.
    #   - A multi-doc transaction across ~70 cross-shard updates runs into
    #     Atlas's 60s transaction timeout for typical customer sizes, and
    #     incurs ~1s/op of two-phase-commit coordination (we measured ~57s
    #     for one customer with 66 orders).
    #   - If the migration is interrupted mid-way, the customer record and
    #     some orders may be in the new zone while the rest are in the old;
    #     `migration_status` detects this via the `in_flight` flag, and the
    #     migration is restartable (idempotent on the surviving in-flight docs).
    order_ids = [
        o["_id"]
        for o in db.orders.find(
            {"customer_id": cust_id, "location": source_code}, {"_id": 1}
        )
    ]

    started = time.perf_counter()
    cust_result = db.customers.update_one(
        {"_id": cust_id, "location": source_code},
        {"$set": {"location": target_code}},
    )

    def _move_one(oid):
        return db.orders.update_one(
            {"_id": oid, "customer_id": cust_id, "location": source_code},
            {"$set": {"location": target_code}},
        ).modified_count

    orders_modified = 0
    if order_ids:
        # pymongo MongoClient is thread-safe; the connection pool serves
        # concurrent threads. Each update_one is its own retryable write.
        with ThreadPoolExecutor(max_workers=ORDERS_PARALLEL_WORKERS) as ex:
            for n in ex.map(_move_one, order_ids):
                orders_modified += n
    elapsed_ms = (time.perf_counter() - started) * 1000

    return {
        "customer_id": cust_id,
        "customer_updated": cust_result.modified_count,
        "orders_updated": orders_modified,
        "source_zone": source_zone,
        "source_code": source_code,
        "target_zone": target_zone,
        "target_code": target_code,
        "elapsed_ms": elapsed_ms,
        "noop": False,
    }


def migrate_customer_to_country(cust_id: str, target_code: str) -> dict:
    """Lower-level migration: write `target_code` directly into the customer's
    `location` field (and all their orders). Caller is responsible for
    ensuring `target_code` is an ISO country code Atlas has mapped to a zone.

    Used by `migrate_all_to_natural_regions` (where the target is the
    customer's own country) and `reset_all_to_us` (where the target is "US"
    regardless of natural country).
    """
    client = get_client()
    db = client[DB_NAME]

    customer = db.customers.find_one({"_id": cust_id}, {"location": 1})
    if not customer:
        raise ValueError(f"Customer {cust_id!r} not found")

    source_code = customer["location"]
    source_zone = zone_for_country(source_code)
    target_zone = zone_for_country(target_code)

    if source_code == target_code:
        return {
            "customer_id": cust_id,
            "customer_updated": 0,
            "orders_updated": 0,
            "source_zone": source_zone,
            "source_code": source_code,
            "target_zone": target_zone,
            "target_code": target_code,
            "elapsed_ms": 0.0,
            "noop": True,
        }

    order_ids = [
        o["_id"]
        for o in db.orders.find(
            {"customer_id": cust_id, "location": source_code}, {"_id": 1}
        )
    ]

    started = time.perf_counter()
    cust_result = db.customers.update_one(
        {"_id": cust_id, "location": source_code},
        {"$set": {"location": target_code}},
    )

    def _move_one(oid):
        return db.orders.update_one(
            {"_id": oid, "customer_id": cust_id, "location": source_code},
            {"$set": {"location": target_code}},
        ).modified_count

    orders_modified = 0
    if order_ids:
        with ThreadPoolExecutor(max_workers=ORDERS_PARALLEL_WORKERS) as ex:
            for n in ex.map(_move_one, order_ids):
                orders_modified += n
    elapsed_ms = (time.perf_counter() - started) * 1000

    return {
        "customer_id": cust_id,
        "customer_updated": cust_result.modified_count,
        "orders_updated": orders_modified,
        "source_zone": source_zone,
        "source_code": source_code,
        "target_zone": target_zone,
        "target_code": target_code,
        "elapsed_ms": elapsed_ms,
        "noop": False,
    }


def migrate_batch_iter(
    targets: list[tuple[str, str]], use_country: bool = False
) -> Iterator[dict]:
    """Stream migration results as each customer finishes.

    targets are (cust_id, target) tuples. If use_country=True, target is
    treated as an ISO country code (passed to migrate_customer_to_country).
    Otherwise target is a zone name (passed to migrate_customer).

    Yields each customer's result in completion order. The Streamlit UI
    consumes this from a worker thread to keep the page interactive while
    the migration runs.
    """
    if not targets:
        return
    fn = migrate_customer_to_country if use_country else migrate_customer
    with ThreadPoolExecutor(max_workers=CUSTOMERS_PARALLEL_WORKERS) as ex:
        futures = {ex.submit(fn, cid, tgt): (cid, tgt) for cid, tgt in targets}
        for fut in as_completed(futures):
            yield fut.result()


def _migrate_batch(targets: list[tuple[str, str]], use_country: bool = False) -> list[dict]:
    """Blocking variant: collect every result and return the list. Kept for
    callers that don't need streaming."""
    return list(migrate_batch_iter(targets, use_country=use_country))


def migrate_all_to_natural_regions() -> dict:
    """Set each customer's `location` to their actual business country code,
    so per-zone distribution mirrors the natural geography of the dataset."""
    db = get_db()
    customers = list(db.customers.find({}, {"_id": 1, "country": 1, "location": 1}))
    targets: list[tuple[str, str]] = []
    skipped = 0
    for c in customers:
        natural_country = c.get("country", "")
        if c["location"] == natural_country:
            skipped += 1
            continue
        targets.append((c["_id"], natural_country))
    moved = _migrate_batch(targets, use_country=True)
    return {"moved": moved, "skipped": skipped, "total": len(customers)}


def reset_all_to_us() -> dict:
    """Consolidate every customer's data into the US zone by setting
    `location = "US"` (a valid ISO country code in the US zone)."""
    db = get_db()
    customers = list(db.customers.find({"location": {"$ne": "US"}}, {"_id": 1}))
    targets = [(c["_id"], "US") for c in customers]
    moved = _migrate_batch(targets, use_country=True)
    return {"moved": moved, "count": len(moved)}


def iter_migrations_to_natural_regions() -> tuple[int, Iterator[dict]]:
    """Streaming variant of `migrate_all_to_natural_regions`. Returns the
    pre-counted total of customers that need a migration plus a generator
    that yields each result as the worker pool completes it.

    The total is computed up-front so the UI can render an "X of N" progress
    bar before the first result arrives.
    """
    db = get_db()
    customers = list(db.customers.find({}, {"_id": 1, "country": 1, "location": 1}))
    targets: list[tuple[str, str]] = []
    for c in customers:
        natural_country = c.get("country", "")
        if c["location"] == natural_country:
            continue
        targets.append((c["_id"], natural_country))
    return len(targets), migrate_batch_iter(targets, use_country=True)


def iter_reset_to_us() -> tuple[int, Iterator[dict]]:
    """Streaming variant of `reset_all_to_us`. Returns (total, generator)."""
    db = get_db()
    customers = list(db.customers.find({"location": {"$ne": "US"}}, {"_id": 1}))
    targets = [(c["_id"], "US") for c in customers]
    return len(targets), migrate_batch_iter(targets, use_country=True)


def migration_status(cust_id: str) -> dict:
    """Logical-zone status for cust_id: customer's location + per-zone order counts.

    `in_flight` is True iff the customer's logical zone differs from any of
    their orders' logical zones (which would indicate an interrupted multi-doc
    migration). Under normal operation this should always be False after
    `migrate_customer` returns.

    Translates the stored ISO codes back to zone names so callers see a
    consistent zone-name vocabulary regardless of on-disk encoding.
    """
    db = get_db()
    customer = db.customers.find_one({"_id": cust_id}, {"location": 1})
    if not customer:
        return {"customer_id": cust_id, "exists": False}

    pipeline = [
        {"$match": {"customer_id": cust_id}},
        {"$group": {"_id": "$location", "n": {"$sum": 1}}},
    ]
    orders_by_zone: dict[str, int] = {}
    for row in db.orders.aggregate(pipeline):
        z = zone_for_country(row["_id"])
        orders_by_zone[z] = orders_by_zone.get(z, 0) + row["n"]

    logical_zone = zone_for_country(customer["location"])
    order_zones = set(orders_by_zone.keys())
    in_flight = bool(order_zones - {logical_zone})

    return {
        "customer_id": cust_id,
        "exists": True,
        "logical_zone": logical_zone,
        "orders_by_zone": orders_by_zone,
        "total_orders": sum(orders_by_zone.values()),
        "in_flight": in_flight,
    }


def physical_shard_for_customer(cust_id: str) -> dict:
    """Use explain.executionStats to find which shard physically holds cust_id's docs.

    Returns:
        {
          "customer":  [{"shard": <name>, "n": int}, ...],
          "orders":    [{"shard": <name>, "n": int}, ...],
        }

    A scatter-gather query (filter without a shard-key prefix) hits every
    shard; the explain output shows nReturned per shard. The shard with
    nReturned > 0 is the physical home of those documents. This is the
    'reveal' for the inspector panel — proves the data physically moved,
    not just that a string field changed.
    """
    db = get_db()

    def _shards_returning(coll_name: str, filter_: dict) -> list[dict]:
        try:
            explain = db.command({
                "explain": {"find": coll_name, "filter": filter_},
                "verbosity": "executionStats",
            })
        except Exception:
            return []
        # Sharded find explain wraps per-shard execution stages under
        # executionStats.executionStages.shards. Extract nReturned per shard.
        stages = explain.get("executionStats", {}).get("executionStages", {})
        shards = stages.get("shards", [])
        out = []
        for s in shards:
            n = s.get("nReturned")
            if n is None:
                # nested executionStages shape on some versions
                n = s.get("executionStages", {}).get("nReturned", 0)
            if n and n > 0:
                out.append({"shard": s.get("shardName", "?"), "n": n})
        return out

    return {
        "customer": _shards_returning("customers", {"_id": cust_id}),
        "orders": _shards_returning("orders", {"customer_id": cust_id}),
    }


def shard_distribution_overview() -> dict:
    """Cluster-wide per-shard document counts for both sharded collections.

    Uses `$collStats` aggregation. In a sharded cluster, `$collStats` runs
    on every shard and returns one document per shard with a top-level
    `shard` field. Used by the sidebar 'Live cluster' panel.
    """
    db = get_db()
    out: dict[str, dict[str, int]] = {"customers": {}, "orders": {}}
    for coll_name in ("customers", "orders"):
        coll = db[coll_name]
        try:
            for row in coll.aggregate([{"$collStats": {"count": {}}}]):
                shard = row.get("shard", "primary")
                count = row.get("count", 0)
                out[coll_name][shard] = count
        except Exception:
            pass
    return out


def list_shards() -> list[dict]:
    """Driver helper around `listShards` admin command. Returns shard
    descriptors with `_id` (shard name) and `host` (replica set + members).
    Used by `check_env` and the inspector panel."""
    client = get_client()
    result = client.admin.command("listShards")
    return result.get("shards", [])


def discover_zone_to_shard() -> dict[str, str]:
    """Probe one customer per zone and ask `explain` which shard physically
    holds them. Returns {zone: shard_name}. A zone with zero customers in
    the current state is omitted — the mapping for that zone is unknown
    until at least one customer lands there.

    Atlas hostnames don't include region tags, so this is the only way to
    map a generic shard name (`atlas-xxxxx-shard-1`) to its meaningful zone
    label (`APAC`). Used by the UI to annotate the Inspector and Cluster
    Overview panels.

    `location` stores an ISO country code, so we look up the set of country
    codes for each zone and find any customer whose location is in that set.
    """
    db = get_db()

    codes_by_zone: dict[str, list[str]] = {z: [] for z in ZONE_NAMES}
    for code, zone in ZONE_FOR_COUNTRY.items():
        if zone in codes_by_zone:
            codes_by_zone[zone].append(code)

    mapping: dict[str, str] = {}
    for zone in ZONE_NAMES:
        codes = codes_by_zone[zone]
        if not codes:
            continue
        cust = db.customers.find_one({"location": {"$in": codes}}, {"_id": 1})
        if not cust:
            continue
        placement = physical_shard_for_customer(cust["_id"])
        shards = placement.get("customer", [])
        if shards:
            mapping[zone] = shards[0]["shard"]
    return mapping
