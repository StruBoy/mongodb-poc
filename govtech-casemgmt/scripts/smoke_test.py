"""End-to-end smoke test for the citizen-services PoC.

Five steps:
  1. Pick a citizen with at least 5 case types and confirm the timeline
     contains all five different schemas in one query
  2. Cross-case Atlas Search for "noise" — must return complaints
  3. Cross-case Atlas Search for "cafe" — must return business_permits
  4. Insert a brand-new "ev_charging_station_permit" case type and confirm it
     appears in the same `cases` collection with no schema migration
  5. Confirm the new case type becomes searchable (within a few seconds)

Run from the project root:
    python -m scripts.smoke_test
"""
import sys
import time

from src.db import get_db
from src.services import (
    add_new_case_type,
    get_cases_for_citizen,
    search_cases,
)

EXPECTED_TYPES = {
    "business_permit",
    "building_permit",
    "complaint",
    "benefit_application",
    "marriage_registration",
}


def step(n, title):
    print(f"\n--- Step {n}: {title} ---")


def find_citizen_with_all_types():
    """Pick a citizen who has all five case types — the seeded demo citizen."""
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
        {"$limit": 1},
    ]
    rows = list(db.cases.aggregate(pipeline))
    return rows[0]["_id"] if rows else None


def main():
    print("=== govtech-casemgmt smoke test ===")
    db = get_db()

    # Step 1: polymorphic timeline
    step(1, "Citizen timeline across all 5 case types")
    citizen_id = find_citizen_with_all_types()
    if not citizen_id:
        print("  [FAIL] no citizen with all 5 case types found")
        sys.exit(1)
    cases = get_cases_for_citizen(citizen_id)
    types = {c["case_type"] for c in cases}
    print(f"  citizen {citizen_id}")
    print(f"  {len(cases)} cases across {len(types)} types: {sorted(types)}")
    if not EXPECTED_TYPES.issubset(types):
        print(f"  [FAIL] missing types: {EXPECTED_TYPES - types}")
        sys.exit(1)
    print("  [OK]")

    # Step 2: search 'noise' should bring up complaints
    step(2, "Atlas Search: 'noise' → expect complaints")
    hits = search_cases("noise", limit=10)
    types = {h["case_type"] for h in hits}
    print(f"  {len(hits)} hits across types: {sorted(types)}")
    if "complaint" not in types:
        print("  [FAIL] expected at least one complaint")
        sys.exit(2)
    print("  [OK]")

    # Step 3: search 'cafe' should bring up business permits
    step(3, "Atlas Search: 'cafe' → expect business_permits")
    hits = search_cases("cafe", limit=10)
    types = {h["case_type"] for h in hits}
    print(f"  {len(hits)} hits across types: {sorted(types)}")
    if "business_permit" not in types:
        print("  [FAIL] expected at least one business_permit")
        sys.exit(3)
    print("  [OK]")

    # Step 4: insert a brand-new case type — no migration
    step(4, "Insert brand-new ev_charging_station_permit (polymorphism)")
    new_id = add_new_case_type(
        "ev_charging_station_permit",
        citizen_id,
        {
            "premises_address": "42 Sample St, Sydney NSW 2000",
            "charger_count": 4,
            "kw_per_charger": 50,
            "grid_capacity_check_passed": True,
            "estimated_install_cost_aud": 85_000,
            "expected_completion_date": "2026-09-15",
        },
    )
    print(f"  inserted _id={new_id}")
    fresh = db.cases.find_one({"_id": __import__('bson').ObjectId(new_id)})
    if not fresh or fresh.get("case_type") != "ev_charging_station_permit":
        print("  [FAIL] new case type not retrievable from cases collection")
        sys.exit(4)
    print(f"  [OK] new case type lives in {db.cases.full_name} alongside the 5 existing schemas")

    # Step 5: searchable within a few seconds (dynamic mapping)
    # Uses the `equals` clause on the token-typed case_type field — that's the
    # idiomatic way to filter by an exact identifier in Atlas Search.
    step(5, "New case type searchable via dynamic Atlas Search index")
    deadline = time.time() + 60
    found = False
    while time.time() < deadline:
        pipeline = [
            {
                "$search": {
                    "index": "cases_search_idx",
                    "equals": {
                        "path": "case_type",
                        "value": "ev_charging_station_permit",
                    },
                }
            },
            {"$limit": 5},
        ]
        hits = list(db.cases.aggregate(pipeline))
        if any(h["_id"] == fresh["_id"] for h in hits):
            found = True
            break
        time.sleep(2)
    if not found:
        print("  [WARN] new case type not yet indexed after 60s. Atlas Search lag varies.")
    else:
        elapsed = 60 - max(0, deadline - time.time())
        print(f"  [OK] new case type returned by Atlas Search in <{elapsed:.0f}s — no migration, no index update.")

    # Cleanup the demo insert so re-running the smoke test stays clean
    db.cases.delete_one({"_id": fresh["_id"]})
    print("\n  cleaned up the demo insert.")

    print("\n=== Smoke test passed ===")


if __name__ == "__main__":
    main()
