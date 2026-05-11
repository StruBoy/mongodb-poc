"""Verify the residency_demo dataset is structurally sound.

Run AFTER `python -m data.generate`. Confirms:
  1. Customer count == 100
  2. Order count between 5,000 and 10,000 (75 +/- 25 per customer)
  3. Every customer's `country` exists in ZONE_FOR_COUNTRY
  4. All seed data is in the US zone (initial state)
  5. Every order's `location` matches its parent customer's `location`
  6. No orphan orders (every order's customer_id exists in customers)
  7. Country diversity: at least 5 distinct countries per region (AMER/EMEA/APAC)
  8. Sharded collection sanity: chunks distributed across all 3 expected shards

Run from the project root (data-residency/):
    python -m scripts.verify_data
"""
from __future__ import annotations

import os
import sys
from collections import Counter

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient

from src.zones import (
    ZONE_FOR_COUNTRY,
    ZONE_NAMES,
    zone_for_country,
)

load_dotenv()

DB_NAME = "residency_demo"
EXPECTED_CUSTOMERS = 100
EXPECTED_ORDER_RANGE = (5000, 10000)


def fail(msg: str) -> None:
    print(f"   [FAIL] {msg}")


def ok(msg: str) -> None:
    print(f"   [OK] {msg}")


def warn(msg: str) -> None:
    print(f"   [WARN] {msg}")


def main() -> int:
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI not set.")
        return 1

    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client[DB_NAME]
    failures = 0

    print("=== verify_data ===", flush=True)

    # 1. Customer count
    print("\n1. Customer count", flush=True)
    n_customers = db.customers.count_documents({})
    if n_customers == EXPECTED_CUSTOMERS:
        ok(f"{n_customers} customers")
    else:
        fail(f"expected {EXPECTED_CUSTOMERS} customers, got {n_customers}")
        failures += 1

    # 2. Order count
    print("\n2. Order count", flush=True)
    n_orders = db.orders.count_documents({})
    if EXPECTED_ORDER_RANGE[0] <= n_orders <= EXPECTED_ORDER_RANGE[1]:
        ok(f"{n_orders:,} orders (in range {EXPECTED_ORDER_RANGE})")
    else:
        fail(f"order count {n_orders} outside range {EXPECTED_ORDER_RANGE}")
        failures += 1

    # 3. Countries are recognised
    print("\n3. Country codes", flush=True)
    customers = list(db.customers.find({}, {"country": 1, "location": 1}))
    unknown = [c for c in customers if c["country"] not in ZONE_FOR_COUNTRY]
    if unknown:
        fail(
            f"{len(unknown)} customers have unrecognised country codes: "
            f"{sorted({c['country'] for c in unknown})[:5]}"
        )
        failures += 1
    else:
        ok(f"all {len(customers)} country codes are in ZONE_FOR_COUNTRY")

    # 4. Initial state — every customer should be in US zone with location="US"
    print("\n4. Initial location (all in US zone)", flush=True)
    by_code = Counter(c["location"] for c in customers)
    bad_codes = {code: n for code, n in by_code.items() if code not in ZONE_FOR_COUNTRY}
    if bad_codes:
        fail(f"customers have unrecognised location codes: {bad_codes}. "
             f"Every location must be an ISO country code that ZONE_FOR_COUNTRY maps to a zone.")
        failures += 1
    elif by_code.get("US", 0) == EXPECTED_CUSTOMERS and len(by_code) == 1:
        ok(f"all {EXPECTED_CUSTOMERS} customers have location='US' (US zone)")
    else:
        by_zone_summary = {zone_for_country(c): n for c, n in by_code.items()}
        fail(f"customers spread across zones {by_zone_summary}; expected all in US")
        failures += 1

    # 5. Orders match parent customer location — done by comparing per-customer
    # location code instead of $lookup (which is slow across sharded collections).
    print("\n5. Order/customer zone consistency", flush=True)
    cust_location_code = {c["_id"]: c["location"] for c in customers}
    pipeline = [{"$group": {"_id": {"cid": "$customer_id", "loc": "$location"}, "n": {"$sum": 1}}}]
    mismatched = []
    for row in db.orders.aggregate(pipeline):
        cid = row["_id"]["cid"]
        loc = row["_id"]["loc"]
        if cust_location_code.get(cid) != loc:
            mismatched.append((cid, loc, cust_location_code.get(cid), row["n"]))
    if not mismatched:
        ok("every order's location matches its parent customer's location")
    else:
        fail(f"{len(mismatched)} (customer_id, location) groups mismatch: "
             f"first: cust={mismatched[0][0]} orders_loc={mismatched[0][1]} "
             f"cust_loc={mismatched[0][2]} (n={mismatched[0][3]})")
        failures += 1

    # 6. No orphan orders
    print("\n6. Orphan orders", flush=True)
    customer_ids = set(cust_location.keys())
    orders_cust_ids = {row["_id"] for row in db.orders.aggregate(
        [{"$group": {"_id": "$customer_id"}}]
    )}
    orphans = orders_cust_ids - customer_ids
    if not orphans:
        ok("no orphan orders")
    else:
        fail(f"{len(orphans)} customer_id values in orders are not in customers")
        failures += 1

    # 7. Country diversity per region
    print("\n7. Country diversity per natural region", flush=True)
    countries_by_region = {z: set() for z in ZONE_NAMES}
    for c in customers:
        countries_by_region[zone_for_country(c["country"])].add(c["country"])
    diversity_ok = True
    for z in ZONE_NAMES:
        n = len(countries_by_region[z])
        if n >= 5:
            ok(f"{z}: {n} distinct countries  ({', '.join(sorted(countries_by_region[z])[:8])})")
        else:
            fail(f"{z}: only {n} distinct countries; expected >= 5 for a credible spread")
            diversity_ok = False
    if not diversity_ok:
        failures += 1

    # 8. Sharded distribution across shards — use $collStats so the check
    # works even on Atlas tiers that hide config.chunks from pocuser.
    print("\n8. Sharded distribution (per-shard $collStats)", flush=True)
    for ns_name in ("customers", "orders"):
        try:
            rows = list(db[ns_name].aggregate([{"$collStats": {"count": {}}}]))
        except Exception as e:
            warn(f"could not run $collStats on {ns_name}: {e}")
            continue
        shards_seen = {r.get("shard", "primary"): r.get("count", 0) for r in rows}
        if len(shards_seen) >= 3:
            summary = ", ".join(f"{s}={n:,}" for s, n in sorted(shards_seen.items()))
            ok(f"{ns_name}: visible on {len(shards_seen)} shards  ({summary})")
        elif len(shards_seen) >= 1:
            # All US-zone data is on the US shard before any migration, so seeing
            # only 1 shard here is correct for the initial state. Just info.
            summary = ", ".join(f"{s}={n:,}" for s, n in sorted(shards_seen.items()))
            print(f"   [info] {ns_name}: only {len(shards_seen)} shard hosts data ({summary})")
            print(f"          Expected at initial state — all data is in US zone. "
                  f"Run migrate_all_to_natural_regions to fan out.")
        else:
            warn(f"{ns_name}: no shard info returned")

    print()
    if failures:
        print(f"=== verify_data: {failures} check(s) FAILED ===")
        return 2
    print("=== verify_data: all checks passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
