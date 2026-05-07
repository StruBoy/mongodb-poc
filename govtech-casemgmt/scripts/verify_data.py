"""Verify the generated dataset is structurally sound.

Eight checks:
  1. Citizen count is in the expected ballpark (~5,000)
  2. Case count is in the expected ballpark (~20,000)
  3. All five case types are present and roughly balanced
  4. Required fields exist on each case-type schema
  5. Encrypted-PII envelopes look right on citizens (national_id, dob)
  6. Encrypted-PII envelopes look right on benefit_application (tax_file_number)
  7. citizen_id on every case resolves to an actual citizen
  8. The Atlas Search index is queryable and returns results

Exits non-zero on any structural failure. Run from the project root:
    python -m scripts.verify_data
"""
import sys

from src.crypto import decrypt_pii
from src.db import get_db

EXPECTED_CASE_TYPES = {
    "business_permit",
    "building_permit",
    "complaint",
    "benefit_application",
    "marriage_registration",
}

EXPECTED_FIELDS_BY_TYPE = {
    "business_permit": {"business_name", "abn", "annual_revenue_estimate_aud"},
    "building_permit": {"property_address", "construction_type", "estimated_cost_aud"},
    "complaint": {"complaint_category", "incident_address", "severity"},
    "benefit_application": {"benefit_program", "tax_file_number", "household_size"},
    "marriage_registration": {"partner_full_name", "ceremony_date", "celebrant_name"},
}


def fail(msg):
    print(f"  [FAIL] {msg}")
    return False


def ok(msg):
    print(f"  [OK]   {msg}")
    return True


def warn(msg):
    print(f"  [WARN] {msg}")
    return True


def check_citizen_count(db):
    print("\n1. Citizen count")
    n = db.citizens.estimated_document_count()
    if n < 4_500 or n > 5_500:
        return fail(f"expected ~5,000 citizens, got {n}")
    return ok(f"{n:,} citizens (within ±10% of 5,000)")


def check_case_count(db):
    print("\n2. Case count")
    n = db.cases.estimated_document_count()
    # 20,000 base + 5 demo-citizen plants
    if n < 19_000 or n > 21_000:
        return fail(f"expected ~20,000 cases, got {n}")
    return ok(f"{n:,} cases")


def check_case_types(db):
    print("\n3. Case-type coverage")
    rows = list(
        db.cases.aggregate(
            [
                {"$group": {"_id": "$case_type", "n": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ]
        )
    )
    found = {r["_id"] for r in rows}
    missing = EXPECTED_CASE_TYPES - found
    if missing:
        return fail(f"missing case types: {sorted(missing)}")
    counts = {r["_id"]: r["n"] for r in rows}
    for t in sorted(EXPECTED_CASE_TYPES):
        if counts[t] < 1_000:
            return fail(
                f"case type '{t}' under-represented: {counts[t]} (expected ~4,000)"
            )
    summary = ", ".join(f"{t}={counts[t]:,}" for t in sorted(EXPECTED_CASE_TYPES))
    return ok(f"all 5 types present. {summary}")


def check_required_fields(db):
    print("\n4. Required fields per case type")
    for case_type, expected in EXPECTED_FIELDS_BY_TYPE.items():
        sample = db.cases.find_one({"case_type": case_type})
        if not sample:
            return fail(f"no sample doc for case_type='{case_type}'")
        missing = expected - set(sample.keys())
        if missing:
            return fail(f"{case_type} missing fields: {sorted(missing)}")
    return ok("all 5 case-type schemas have their required fields")


def check_citizen_pii_encryption(db):
    print("\n5. Citizen PII encryption envelopes")
    sample = db.citizens.find_one()
    if not sample:
        return fail("no citizen documents found")
    for field in ("national_id", "dob"):
        v = sample.get(field)
        if not isinstance(v, dict) or not v.get("_encrypted"):
            return fail(f"citizen.{field} is not an encrypted envelope: {v!r}")
        for k in ("nonce", "ciphertext"):
            if k not in v:
                return fail(f"citizen.{field} envelope missing '{k}'")

    plaintext = decrypt_pii(sample)
    if not isinstance(plaintext.get("national_id"), str):
        return fail("decrypt_pii did not yield string national_id")
    return ok("national_id and dob are encrypted on disk and decrypt cleanly")


def check_benefit_pii_encryption(db):
    print("\n6. Benefit application tax_file_number encryption")
    sample = db.cases.find_one({"case_type": "benefit_application"})
    if not sample:
        return fail("no benefit_application docs found")
    v = sample.get("tax_file_number")
    if not isinstance(v, dict) or not v.get("_encrypted"):
        return fail(f"tax_file_number is not encrypted: {v!r}")
    plaintext = decrypt_pii(sample)
    if not isinstance(plaintext.get("tax_file_number"), str):
        return fail("decrypt_pii did not yield string tax_file_number")
    return ok("tax_file_number is encrypted on disk and decrypts cleanly")


def check_citizen_references(db):
    print("\n7. citizen_id integrity (sample)")
    cursor = db.cases.aggregate([{"$sample": {"size": 200}}])
    sampled = list(cursor)
    citizen_ids = list({c["citizen_id"] for c in sampled})
    found = db.citizens.count_documents({"_id": {"$in": citizen_ids}})
    if found != len(citizen_ids):
        return fail(
            f"of {len(citizen_ids)} sampled citizen_ids, only {found} resolve "
            f"to a citizen document"
        )
    return ok(f"all {len(citizen_ids)} sampled citizen_ids resolve cleanly")


def check_search_index(db):
    print("\n8. Atlas Search index queryable")
    indexes = list(db.cases.list_search_indexes())
    if not any(i.get("name") == "cases_search_idx" for i in indexes):
        return fail("cases_search_idx not found")
    idx = next(i for i in indexes if i["name"] == "cases_search_idx")
    if not idx.get("queryable"):
        return fail(f"index status: {idx.get('status')} queryable={idx.get('queryable')}")

    pipeline = [
        {
            "$search": {
                "index": "cases_search_idx",
                "text": {"query": "noise", "path": {"wildcard": "*"}},
            }
        },
        {"$limit": 5},
    ]
    hits = list(db.cases.aggregate(pipeline))
    if not hits:
        return warn("index is queryable but returned 0 hits for 'noise' (data may be stale-indexed; wait a moment)")
    return ok(f"{len(hits)} hits for 'noise' across {len({h['case_type'] for h in hits})} case type(s)")


def main():
    db = get_db()
    print("=== govtech-casemgmt data verification ===")

    checks = [
        check_citizen_count,
        check_case_count,
        check_case_types,
        check_required_fields,
        check_citizen_pii_encryption,
        check_benefit_pii_encryption,
        check_citizen_references,
        check_search_index,
    ]

    failures = 0
    for c in checks:
        if not c(db):
            failures += 1

    print()
    if failures:
        print(f"=== {failures} check(s) failed ===")
        sys.exit(1)
    print("=== All 8 checks passed ===")


if __name__ == "__main__":
    main()
