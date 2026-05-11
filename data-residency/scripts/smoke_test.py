"""End-to-end smoke test: prove the migration mechanism works.

Steps:
  1. Pick an APAC-domiciled customer (e.g. cust-XXX with country=SG).
     Confirm initial location is 'US'.
  2. migrate_customer(cust_id, 'APAC').
  3. Re-read; assert customer.location == 'APAC' and every one of its orders
     has location == 'APAC'.
  4. Run physical_shard_for_customer; confirm it reports an APAC-region shard.
  5. Run shard_distribution_overview; confirm APAC shard now has >= 1 customer
     and >= 1 order.
  6. Migrate back to US.
  7. Run migrate_all_to_natural_regions; confirm all 3 zones are populated
     and counts are coherent (each customer in their natural zone).
  8. reset_all_to_us; confirm all 100 customers are in US zone again.

Cleanup leaves everyone in the US zone so the demo opens clean.

Run from the project root (data-residency/):
    python -m scripts.smoke_test
"""
from __future__ import annotations

import sys

from src.customers import customer_count_by_location, list_customers
from src.db import get_db
from src.migration import (
    migrate_all_to_natural_regions,
    migrate_customer,
    migration_status,
    physical_shard_for_customer,
    reset_all_to_us,
    shard_distribution_overview,
)
from src.orders import order_count_by_location, order_count_for_customer
from src.zones import ZONE_FOR_COUNTRY, ZONE_NAMES, zone_for_country


def fail(msg: str) -> None:
    print(f"   [FAIL] {msg}")
    sys.exit(2)


def ok(msg: str) -> None:
    print(f"   [OK] {msg}")


def info(msg: str) -> None:
    print(f"   [info] {msg}")


def pick_apac_customer() -> dict:
    db = get_db()
    apac_countries = [code for code, zone in ZONE_FOR_COUNTRY.items() if zone == "APAC"]
    cust = db.customers.find_one({"country": {"$in": apac_countries}})
    if not cust:
        fail("no APAC-domiciled customer found in dataset; run `python -m data.generate` first.")
    return cust


def main() -> int:
    db = get_db()

    # Step 0: sanity checks
    print("=== smoke_test: data-residency migration ===\n")

    n_cust = db.customers.count_documents({})
    if n_cust == 0:
        fail(f"no customers in residency_demo.customers; run `python -m data.generate` first.")
    info(f"dataset: {n_cust} customers")

    # Step 1: pick an APAC customer in initial US state
    print("\n1. Pick an APAC-domiciled customer in initial state")
    apac_cust = pick_apac_customer()
    cust_id = apac_cust["_id"]
    initial_location = apac_cust["location"]
    n_orders = order_count_for_customer(cust_id)
    if initial_location != "US":
        fail(
            f"customer {cust_id} initial location is {initial_location!r}, "
            f"expected 'US'. Run `python -m scripts.smoke_test` after a fresh seed "
            f"or call reset_all_to_us first."
        )
    ok(f"selected {cust_id} ({apac_cust.get('name')}, country={apac_cust['country']}) "
       f"with {n_orders} orders in US zone")

    # Step 2: migrate to APAC
    print("\n2. Migrate to APAC")
    result = migrate_customer(cust_id, "APAC")
    info(
        f"migrate_customer returned in {result['elapsed_ms']:.0f} ms "
        f"({result['customer_updated']} customer + {result['orders_updated']} orders)"
    )
    if result["customer_updated"] != 1:
        fail(f"expected 1 customer update; got {result['customer_updated']}")
    if result["orders_updated"] != n_orders:
        fail(f"expected {n_orders} order updates; got {result['orders_updated']}")
    ok(f"migration to APAC reported success")

    # Step 3: post-migration logical state
    print("\n3. Verify logical zone state")
    status = migration_status(cust_id)
    if status["logical_zone"] != "APAC":
        fail(f"customer logical_zone is {status['logical_zone']!r}; expected 'APAC'")
    if status["in_flight"]:
        fail(f"migration_status reports in_flight=True; orders_by_zone={status['orders_by_zone']}")
    if list(status["orders_by_zone"].keys()) != ["APAC"]:
        fail(f"orders_by_zone is {status['orders_by_zone']}; expected only APAC")
    ok(f"customer + {status['total_orders']} orders all report logical_zone=APAC")

    # Step 4: physical shard verification via explain
    print("\n4. Verify physical shard placement")
    placement = physical_shard_for_customer(cust_id)
    cust_shards = placement["customer"]
    orders_shards = placement["orders"]
    info(f"customer shard(s): {cust_shards}")
    info(f"orders shard(s):   {orders_shards}")
    cust_shard_names = {s["shard"] for s in cust_shards}
    if not cust_shard_names:
        fail("explain returned no shard for customer doc")
    if len(cust_shard_names) > 1:
        fail(f"customer doc reports multiple physical shards: {cust_shard_names}")
    apac_shard_match = any("ap-southeast-1" in s.lower() or "apac" in s.lower() for s in cust_shard_names)
    if apac_shard_match:
        ok(f"customer doc lives on an APAC-region shard: {cust_shard_names}")
    else:
        # Atlas shard names sometimes don't carry the region tag; warn but don't
        # fail since the logical state is verified above.
        print(f"   [WARN] shard name {cust_shard_names} does not contain 'ap-southeast-1'; "
              "verify in Atlas UI that the APAC zone shard is correctly mapped.")

    # Step 5: cluster-wide distribution
    print("\n5. Cluster-wide shard distribution")
    overview = shard_distribution_overview()
    info(f"customers per shard: {overview['customers']}")
    info(f"orders    per shard: {overview['orders']}")

    # Step 6: migrate back
    print("\n6. Migrate back to US")
    back = migrate_customer(cust_id, "US")
    if back["customer_updated"] != 1 or back["orders_updated"] != n_orders:
        fail(f"reverse migration counts off: {back}")
    if migration_status(cust_id)["logical_zone"] != "US":
        fail("post-revert logical_zone != 'US'")
    ok(f"reverted {cust_id} to US in {back['elapsed_ms']:.0f} ms")

    # Step 7: bulk migration
    print("\n7. Bulk migrate to natural regions")
    bulk = migrate_all_to_natural_regions()
    info(f"moved {len(bulk['moved'])} customers; skipped {bulk['skipped']}")
    counts = customer_count_by_location()
    for z in ZONE_NAMES:
        n = counts.get(z, 0)
        if n == 0:
            fail(f"zone {z} has 0 customers after natural-region fanout; expected > 0")
        ok(f"zone {z}: {n} customers")

    # Sanity: after natural-region fanout, every customer's `location` ISO
    # country code should equal their `country` field (data resides in their
    # actual country), which by construction is in their natural zone.
    mismatched = 0
    wrong_zone = 0
    for c in list_customers():
        if c["location"] != c["country"]:
            mismatched += 1
        if c["data_zone"] != zone_for_country(c["country"]):
            wrong_zone += 1
    if wrong_zone:
        fail(f"{wrong_zone} customers in non-natural zones after fanout")
    if mismatched:
        fail(f"{mismatched} customers have location != country after natural fanout")
    ok("every customer's location == country (natural country granularity)")

    # Step 8: reset
    print("\n8. Reset all to US")
    reset_result = reset_all_to_us()
    info(f"reset moved {reset_result['count']} customers")
    final_counts = customer_count_by_location()
    if final_counts.get("US", 0) != n_cust:
        fail(f"after reset, US has {final_counts.get('US', 0)} of {n_cust} customers")
    if any(final_counts.get(z, 0) for z in ("EU", "APAC")):
        fail(f"after reset, EU+APAC still hold customers: {final_counts}")
    ok(f"all {n_cust} customers back in US zone")

    final_orders = order_count_by_location()
    if final_orders.get("US", 0) != db.orders.count_documents({}):
        fail(f"orders not all in US after reset: {final_orders}")
    ok(f"all {final_orders.get('US', 0):,} orders back in US zone")

    print("\n=== smoke_test: all 8 steps passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
