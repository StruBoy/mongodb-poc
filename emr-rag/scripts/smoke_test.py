"""End-to-end smoke test. Proves the prognosis arc lands automatically.

Steps:
  1. Generate clinical summary BEFORE follow-ups; assert no confident OSA diagnosis.
  2. Ask "What's driving her sleep issues?" — assert top source is a psychiatry visit.
  3. add_followup_batch — assert visits collection grows by 4, including sleep_medicine.
  4. Re-summarize — assert it now diagnoses OSA explicitly and uses unifying language.
  5. Ask "What's the CPAP plan?" — assert top source is the sleep_medicine visit.
  6. Re-ask the sleep question — assert sleep_medicine visit now appears in the answer's sources.

Run from the project root (emr-rag/):
    python -m scripts.smoke_test
"""
import sys

from src.db import get_db
from src.rag import answer_question, summarize_patient
from src.visits import add_followup_batch, remove_followup_batch

PATIENT_ID = "pat-001"

OSA_DIAGNOSTIC_TERMS = ("ahi", "polysomnograph", "cpap")
OSA_DIAGNOSIS_TERMS = ("obstructive sleep apno", "sleep apnoea", "sleep apnea")
UNIFYING_TERMS = ("unif", "upstream", "drives", "driving", "explains", "underlying", "root cause")


def hr(label: str = ""):
    print("\n" + "=" * 78)
    if label:
        print(label)
        print("-" * 78)


def fail(msg: str):
    print(f"\n[FAIL] {msg}")
    print("\nCleaning up follow-up inserts before exit...")
    try:
        n = remove_followup_batch()
        print(f"  removed {n} visit(s).")
    except Exception as e:
        print(f"  cleanup failed: {e}")
    sys.exit(2)


def main() -> int:
    db = get_db()

    # Pre-flight: corpus must be seeded
    visits_initial = db.visits.count_documents({"patient_id": PATIENT_ID})
    if visits_initial == 0:
        print("[FAIL] No visits in emr_demo for pat-001. Run `python -m data.generate` first.")
        return 1
    if visits_initial != 6:
        print(f"[WARN] Expected 6 seed visits, found {visits_initial}. "
              f"Cleaning any stale follow-ups before testing.")
        remove_followup_batch()
        visits_initial = db.visits.count_documents({"patient_id": PATIENT_ID})
    print(f"Pre-flight: {visits_initial} seed visits present for {PATIENT_ID}")

    # ----- Step 1: BEFORE summary -----
    hr("Step 1: clinical summary BEFORE follow-up batch")
    summary_before = summarize_patient(PATIENT_ID, "clinical")
    print(summary_before["summary"])
    text_before = summary_before["summary"].lower()
    diagnostic_hits = [t for t in OSA_DIAGNOSTIC_TERMS if t in text_before]
    if diagnostic_hits:
        fail(f"BEFORE summary contains OSA diagnostic terms it shouldn't: {diagnostic_hits}")
    print(f"\n[OK] retrieval={summary_before['retrieval_ms']:.0f}ms "
          f"generation={summary_before['generation_ms']:.0f}ms; "
          f"no confident OSA diagnosis (no AHI / polysomnography / CPAP).")

    # ----- Step 2: BEFORE Q&A -----
    hr("Step 2: ask 'What's driving her poor sleep?' BEFORE follow-up batch")
    q_sleep_before = answer_question(PATIENT_ID, "What's driving her poor sleep?", "clinical")
    print(q_sleep_before["answer"])
    print()
    print("Sources:")
    for s in q_sleep_before["sources"]:
        print(f"  - {s['id']} ({s['specialty']}) score={s['score']:.4f}")
    if not any(s["specialty"] == "psychiatry" for s in q_sleep_before["sources"]):
        fail("BEFORE sleep question did not surface any psychiatry visit")
    print("\n[OK] psychiatry visit surfaced as expected.")

    # ----- Step 3: add follow-ups -----
    hr("Step 3: add_followup_batch")
    inserted = add_followup_batch(PATIENT_ID)
    print(f"Inserted: {inserted}")
    visits_after = db.visits.count_documents({"patient_id": PATIENT_ID})
    if visits_after != visits_initial + 4:
        fail(f"Expected {visits_initial + 4} visits after follow-up batch, got {visits_after}")
    sleep_med_present = db.visits.count_documents(
        {"patient_id": PATIENT_ID, "specialty": "sleep_medicine"}
    )
    if sleep_med_present != 1:
        fail(f"Expected 1 sleep_medicine visit after batch, got {sleep_med_present}")
    print(f"[OK] visits={visits_after}, including 1 sleep_medicine visit (novel specialty).")

    # ----- Step 4: AFTER summary -----
    hr("Step 4: clinical summary AFTER follow-up batch")
    summary_after = summarize_patient(PATIENT_ID, "clinical")
    print(summary_after["summary"])
    text_after = summary_after["summary"].lower()
    diagnosis_hits = [t for t in OSA_DIAGNOSIS_TERMS if t in text_after]
    if not diagnosis_hits:
        fail(f"AFTER summary doesn't mention OSA / sleep apnea (any of {OSA_DIAGNOSIS_TERMS})")
    diagnostic_evidence = [t for t in OSA_DIAGNOSTIC_TERMS if t in text_after]
    if not diagnostic_evidence:
        fail(f"AFTER summary doesn't reference diagnostic evidence (any of {OSA_DIAGNOSTIC_TERMS})")
    unifying_hits = [t for t in UNIFYING_TERMS if t in text_after]
    if not unifying_hits:
        fail(f"AFTER summary doesn't frame OSA as unifying (any of {UNIFYING_TERMS})")
    print(f"\n[OK] retrieval={summary_after['retrieval_ms']:.0f}ms "
          f"generation={summary_after['generation_ms']:.0f}ms; "
          f"OSA named ({diagnosis_hits}), evidence cited ({diagnostic_evidence}), "
          f"framed as unifying ({unifying_hits}).")

    # Side-by-side
    hr("Prognosis shift — side-by-side")
    print("BEFORE:\n" + summary_before["summary"])
    print("\nAFTER:\n" + summary_after["summary"])

    # ----- Step 5: CPAP plan question -----
    hr("Step 5: ask 'What is her CPAP plan?'")
    q_cpap = answer_question(PATIENT_ID, "What is her CPAP plan?", "clinical")
    print(q_cpap["answer"])
    print("\nSources:")
    for s in q_cpap["sources"]:
        print(f"  - {s['id']} ({s['specialty']}) score={s['score']:.4f}")
    top = q_cpap["sources"][0] if q_cpap["sources"] else None
    if not top or top["specialty"] != "sleep_medicine":
        fail(f"Expected sleep_medicine as top source for CPAP question; got {top}")
    print("\n[OK] sleep_medicine visit is top source — novel specialty indexed end-to-end.")

    # ----- Step 6: AFTER sleep question -----
    hr("Step 6: re-ask 'What's driving her poor sleep?' AFTER follow-up batch")
    q_sleep_after = answer_question(PATIENT_ID, "What's driving her poor sleep?", "clinical")
    print(q_sleep_after["answer"])
    print("\nSources:")
    for s in q_sleep_after["sources"]:
        print(f"  - {s['id']} ({s['specialty']}) score={s['score']:.4f}")
    if not any(s["specialty"] == "sleep_medicine" for s in q_sleep_after["sources"]):
        fail("AFTER sleep question did not surface the sleep_medicine visit")
    print("\n[OK] sleep_medicine visit surfaced — same question, fundamentally better answer.")

    # ----- Cleanup -----
    hr("Cleanup")
    n = remove_followup_batch()
    print(f"Removed {n} follow-up visit(s). Demo opens clean.")

    print("\n=== All 6 steps passed. Prognosis arc lands automatically. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
