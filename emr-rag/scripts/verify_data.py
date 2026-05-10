"""Confirm the seeded patient + visits look right after `python -m data.generate`.

Run from the project root (emr-rag/):
    python -m scripts.verify_data
"""
import sys

from src.crypto import decrypt_field
from src.db import get_db
from src.patients import get_patient

REQUIRED_VISIT_FIELDS = {
    "patient_id", "visit_date", "specialty", "provider", "chief_complaint",
    "body", "embedding", "last_updated",
}
EXPECTED_SEED_SPECIALTIES = {"general_practice", "psychiatry", "cardiology", "endocrinology"}
EXPECTED_PATIENT_ID = "pat-001"
EXPECTED_VISIT_COUNT = 6
EXPECTED_EMBEDDING_DIM = 1024
SHORT_BODY_THRESHOLD = 400  # fallback bodies are way shorter than Claude-generated ones


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "[OK]  " if ok else "[FAIL]"
    print(f"  {status} {label}{(' — ' + detail) if detail else ''}")
    return ok


def main() -> int:
    db = get_db()
    fails = 0

    print("1. Patient present and PHI envelopes round-trip")
    raw = db.patients.find_one({"_id": EXPECTED_PATIENT_ID})
    if not check("demo patient exists", raw is not None, EXPECTED_PATIENT_ID):
        return 1
    pii_keys_present = all(
        isinstance(raw.get(k), dict) and raw[k].get("_encrypted")
        for k in ("national_id", "dob", "insurance_id")
    )
    if not check("PHI fields are encrypted at rest", pii_keys_present):
        fails += 1
    decrypted = get_patient(EXPECTED_PATIENT_ID)
    if not check(
        "PHI decrypts on read",
        decrypted is not None
        and decrypted.get("national_id", "").startswith("S")
        and "-" in decrypted.get("dob", ""),
        f"national_id={decrypted.get('national_id')}, dob={decrypted.get('dob')}",
    ):
        fails += 1

    print("\n2. Visit count and field shape")
    visits = list(db.visits.find({"patient_id": EXPECTED_PATIENT_ID}))
    if not check(
        f"exactly {EXPECTED_VISIT_COUNT} visits for {EXPECTED_PATIENT_ID}",
        len(visits) == EXPECTED_VISIT_COUNT,
        f"got {len(visits)}",
    ):
        fails += 1
    missing_fields = []
    for v in visits:
        absent = REQUIRED_VISIT_FIELDS - set(v.keys())
        if absent:
            missing_fields.append((v["_id"], absent))
    if not check(
        "all visits have required fields",
        not missing_fields,
        "; ".join(f"{vid}: {fields}" for vid, fields in missing_fields[:3]),
    ):
        fails += 1

    print("\n3. Embedding shape")
    bad_dim = [v["_id"] for v in visits if not isinstance(v.get("embedding"), list)
               or len(v["embedding"]) != EXPECTED_EMBEDDING_DIM]
    if not check(
        f"embeddings are {EXPECTED_EMBEDDING_DIM}-dim lists",
        not bad_dim,
        f"bad: {bad_dim}",
    ):
        fails += 1

    print("\n4. Specialty coverage")
    specialties = {v["specialty"] for v in visits}
    missing = EXPECTED_SEED_SPECIALTIES - specialties
    if not check(
        f"all expected specialties present: {sorted(EXPECTED_SEED_SPECIALTIES)}",
        not missing,
        f"missing: {sorted(missing)}; got: {sorted(specialties)}",
    ):
        fails += 1

    print("\n5. Body length floor (catches fallback templates)")
    short = [(v["_id"], len(v.get("body", ""))) for v in visits
             if len(v.get("body", "")) < SHORT_BODY_THRESHOLD]
    if not check(
        f"all bodies ≥ {SHORT_BODY_THRESHOLD} chars",
        not short,
        f"short: {short}",
    ):
        fails += 1

    print("\n6. Seed contains no premature OSA mentions")
    osa_terms = ("obstructive sleep apno", "AHI", "CPAP", "polysomnogr", "syndrome z")
    leaks = []
    for v in visits:
        text = (v.get("body", "") + " " + v.get("chief_complaint", "")).lower()
        hits = [t for t in osa_terms if t.lower() in text]
        if hits:
            leaks.append((v["_id"], hits))
    if not check(
        "no seed visit mentions OSA / AHI / CPAP / polysomnography / syndrome Z",
        not leaks,
        f"leaks: {leaks}",
    ):
        fails += 1

    print("\n7. Vector index reachable")
    indexes = list(db.visits.list_search_indexes())
    queryable = [i for i in indexes if i.get("queryable")]
    if not check(
        "kb_visits_idx is queryable",
        any(i["name"] == "kb_visits_idx" for i in queryable),
        f"indexes: {[(i['name'], i.get('queryable')) for i in indexes]}",
    ):
        fails += 1

    if fails:
        print(f"\n=== {fails} check(s) failed ===")
        return 2
    print("\n=== All checks passed. Seed corpus looks good. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
