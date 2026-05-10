"""Seed emr_demo with one demo patient (Maria Chen) and 6 fragmented visits.

Each visit fixture carries a structured spec (vitals, scores, labs) plus prose
hooks; Claude haiku 4.5 expands the spec into a 200-350 word realistic clinical
note in the voice of the relevant specialty. The structured fields are stored
alongside `body` so they're queryable directly *and* present in the embedded
text.

The seed picture is intentionally fragmented: prediabetes worsening despite
metformin, anxiety + treatment-resistant insomnia, and palpitations attributed
to anxiety. Nothing in the seed mentions OSA, sleep apnea, snoring, AHI, or
CPAP — that's the diagnostic pivot the follow-up batch unlocks.

Run from the project root (emr-rag/):
    python -m data.generate
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from anthropic import Anthropic
from dotenv import load_dotenv

from src.db import get_db
from src.patients import upsert_patient
from src.visits import upsert_visit

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

GEN_MODEL = "claude-haiku-4-5-20251001"

# --- Demo patient -------------------------------------------------------------

DEMO_PATIENT = {
    "_id": "pat-001",
    "name": "Maria Chen",
    "sex": "F",
    "date_of_birth_age_years": 47,
    "ethnicity": "Singaporean Chinese",
    "bmi": 31.0,
    "height_cm": 162,
    "weight_kg": 81,
    "primary_gp": "Dr. Tan Wei Ling",
    "occupation": "Office worker (finance team)",
    # PHI fields — encrypted on write by upsert_patient via src.crypto
    "national_id": "S7812345A",
    "dob": "1978-08-14",
    "insurance_id": "MOH-AIC-44820193",
}

# --- Seed visit fixtures ------------------------------------------------------
# `days_ago` measured from generate-time. Specialty-specific structured fields
# go into `structured` and pass through verbatim. `prose_hooks` is what Claude
# uses to compose the body — it's the clinical content the body MUST contain.

SEED_VISITS = [
    {
        "_id": "vis-001",
        "days_ago": 240,
        "specialty": "general_practice",
        "provider": "Dr. Tan Wei Ling, GP",
        "chief_complaint": "Annual physical; fatigue and 5kg weight gain over 2 years",
        "structured": {
            "bp_systolic": 138,
            "bp_diastolic": 86,
            "heart_rate_bpm": 78,
            "weight_kg": 81,
            "bmi": 31.0,
            "labs_ordered": ["FBC", "lipid panel", "HbA1c", "TSH", "fasting glucose"],
        },
        "prose_hooks": (
            "47F office worker presenting for annual physical. Reports gradual fatigue and 5kg weight gain "
            "over the last 2 years. No exercise routine; sedentary work. Diet: regular hawker meals, occasional "
            "alcohol (2-3 drinks/week). No tobacco. Family history: father T2DM dx age 55, mother hypertensive. "
            "On exam: BMI 31 (Class I obesity), BP 138/86 mildly elevated, otherwise unremarkable. "
            "Plan: order baseline labs (FBC, lipid panel, HbA1c, TSH, fasting glucose). Lifestyle counselling "
            "given on diet and physical activity. Review labs in 1 week."
        ),
    },
    {
        "_id": "vis-002",
        "days_ago": 210,
        "specialty": "general_practice",
        "provider": "Dr. Tan Wei Ling, GP",
        "chief_complaint": "Lab review; new anxiety symptoms",
        "structured": {
            "hba1c_pct": 6.2,
            "fasting_glucose_mmol": 6.0,
            "ldl_mmol": 3.6,
            "hdl_mmol": 1.1,
            "triglycerides_mmol": 1.8,
            "tsh_miu_l": 2.1,
            "bp_systolic": 136,
            "bp_diastolic": 84,
        },
        "prose_hooks": (
            "Lab review. HbA1c 6.2% (prediabetic range, ADA criteria 5.7-6.4). Fasting glucose 6.0 mmol/L "
            "(impaired fasting glucose). LDL 3.6 mmol/L mildly elevated. TSH 2.1 normal. Patient also volunteers "
            "new-onset anxiety: racing thoughts at night, difficulty falling asleep, early-morning awakening "
            "around 04:30 with inability to return to sleep. Reports feeling 'on edge' at work for ~3 months. "
            "BP today 136/84. Plan: lifestyle intervention for prediabetes (5-7% weight loss target), "
            "Mediterranean-style diet, 150 min/week moderate exercise. Repeat HbA1c in 3 months. "
            "Refer to psychiatry for anxiety + insomnia assessment given functional impact. No statin yet."
        ),
    },
    {
        "_id": "vis-003",
        "days_ago": 180,
        "specialty": "psychiatry",
        "provider": "Dr. Wong Mei Ling, Psychiatrist",
        "chief_complaint": "Initial assessment for anxiety and insomnia",
        "structured": {
            "phq9_score": 11,
            "gad7_score": 13,
            "sleep_quality": "poor",
            "typical_sleep_onset_min": 60,
            "early_morning_awakening": True,
            "medications_started": ["sertraline 50mg daily"],
            "therapy_referral": "CBT-I (cognitive behavioural therapy for insomnia)",
        },
        "prose_hooks": (
            "Initial psychiatric assessment, GP referral. Patient describes 3-4 months of generalised anxiety, "
            "racing thoughts particularly at night, sleep onset latency ~1 hour despite physical tiredness, "
            "early-morning awakening around 04:30 with rumination. PHQ-9 score 11 (moderate depression — driven "
            "primarily by sleep, fatigue, and concentration items rather than mood). GAD-7 score 13 (moderate "
            "anxiety). No suicidal ideation. No prior psychiatric history. Some mild stress at work but no "
            "single precipitant. Bed partner not formally interviewed. Plan: start sertraline 50mg daily, "
            "titrate up if tolerated. Refer for CBT-I (waitlist ~6 weeks). Sleep hygiene counselling provided. "
            "Review in 6 weeks. Discussed expected onset of SSRI effect (4-6 weeks)."
        ),
    },
    {
        "_id": "vis-004",
        "days_ago": 120,
        "specialty": "cardiology",
        "provider": "Dr. Lim Han Wei, Cardiologist",
        "chief_complaint": "Intermittent palpitations and chest tightness",
        "structured": {
            "bp_systolic": 134,
            "bp_diastolic": 84,
            "heart_rate_bpm": 82,
            "ecg_findings": "Sinus rhythm, occasional PVCs, no ST changes",
            "echo_findings": "Normal LV function (EF 62%), no structural disease, mild LA size",
            "holter_ordered": True,
            "troponin_ng_l": 5,
        },
        "prose_hooks": (
            "Referred by GP for evaluation of palpitations and chest tightness. Episodes are brief (seconds), "
            "irregular, occur a few times per week, often when she is trying to sleep or in early morning hours. "
            "Some daytime episodes during stressful periods at work. No syncope, no exertional symptoms, no "
            "exercise intolerance. Resting ECG: sinus rhythm 82 bpm, occasional PVCs, no ischaemic changes. "
            "Echo: structurally normal heart, EF 62%, mild LA enlargement noted incidentally. Troponin 5 ng/L "
            "(unremarkable). BP 134/84. Impression: most likely benign palpitations in the setting of anxiety, "
            "with PVC burden likely contributing to the symptomatic awareness. Plan: 24-hour Holter monitor "
            "to quantify burden and rule out concerning arrhythmias before reassuring definitively. "
            "Continue current psychiatric treatment. No anti-arrhythmic at this time. Follow up with Holter results."
        ),
    },
    {
        "_id": "vis-005",
        "days_ago": 90,
        "specialty": "endocrinology",
        "provider": "Dr. Rahim Abdullah, Endocrinologist",
        "chief_complaint": "Prediabetes management — HbA1c rising despite lifestyle",
        "structured": {
            "hba1c_pct": 6.4,
            "fasting_glucose_mmol": 6.4,
            "weight_kg": 80.5,
            "bmi": 30.7,
            "ldl_mmol": 3.5,
            "medications_started": ["metformin 500mg BID"],
        },
        "prose_hooks": (
            "Referred for prediabetes management. Despite 3 months of dietary modification and intermittent "
            "exercise, HbA1c has crept from 6.2 to 6.4% — at the upper end of the prediabetic range and trending "
            "toward T2DM. Weight essentially unchanged at 80.5kg (BMI 30.7). Fasting glucose 6.4 mmol/L. Patient "
            "reports adherence to dietary changes has been inconsistent; cites fatigue and poor sleep as the main "
            "obstacles to exercise. Family hx of T2DM noted. Decision: initiate metformin 500mg BID with food, "
            "titrate to 1g BID over 4 weeks if tolerated, given trajectory and insulin-resistance picture. "
            "Continue lifestyle measures. Recheck HbA1c in 3 months. No statin started today; will reassess after "
            "weight intervention has had a fair trial. Discussed reasonable expectations: metformin should at "
            "least flatten the trajectory; further progression on optimal therapy would prompt re-evaluation."
        ),
    },
    {
        "_id": "vis-006",
        "days_ago": 60,
        "specialty": "psychiatry",
        "provider": "Dr. Wong Mei Ling, Psychiatrist",
        "chief_complaint": "Psychiatry follow-up; sleep still unrefreshing",
        "structured": {
            "phq9_score": 9,
            "gad7_score": 11,
            "sleep_quality": "poor — unrefreshing despite 7-8h in bed",
            "medications_started": ["trazodone 50mg at night"],
            "medications_changed": ["sertraline increased 50mg → 100mg daily"],
            "cbt_i_completed_sessions": 4,
        },
        "prose_hooks": (
            "Psychiatry follow-up at 4 months. Anxiety modestly improved on sertraline 50mg — GAD-7 from 13 to "
            "11, PHQ-9 from 11 to 9. However, sleep remains the dominant complaint. Patient reports she gets "
            "7-8 hours in bed but wakes feeling unrefreshed; partner reports she 'tosses and turns' through the "
            "night. Completed 4 sessions of CBT-I with reasonable adherence to sleep restriction and stimulus "
            "control protocols, but symptomatic benefit has been limited. Sertraline increased to 100mg daily. "
            "Trazodone 50mg added at night for sleep initiation. Continue CBT-I. Review in 6 weeks. If sleep "
            "doesn't improve on this regimen, consider re-referral to GP for general medical workup."
        ),
    },
]


def now_minus(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def generate_visit_body(spec: dict) -> str:
    """Use Claude to expand a structured spec into a 200-350 word clinical note."""
    structured_block = json.dumps(spec["structured"], indent=2)
    prompt = f"""You are writing a realistic clinical visit note for a {spec['specialty'].replace('_', ' ')} encounter at a Singaporean hospital. Write in the voice of the attending: concise, professional, factual.

Clinical content the note MUST contain (do not omit any of these):
{spec['prose_hooks']}

Structured findings recorded for this visit (incorporate these naturally — DO NOT bullet-point them, weave them into prose):
{structured_block}

Format requirements:
- 200-350 words.
- Sectioned: short HPI / pertinent findings / assessment / plan, written as continuous clinical prose (no markdown headers, no bullet lists).
- Sound like a real clinical note, not a patient-facing explainer.
- Do NOT mention obstructive sleep apnea, sleep apnea, snoring, AHI, polysomnography, CPAP, or "syndrome Z" — none of those are on the differential yet for this visit.
- Do NOT invent labs or findings beyond what's in the structured block and prose hooks.

Return ONLY a JSON object with this shape (no preamble, no fences):
{{"body": "..."}}"""

    response = anthropic.messages.create(
        model=GEN_MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    parsed = json.loads(text)
    return parsed["body"]


def main():
    db = get_db()
    db.patients.delete_many({})
    db.visits.delete_many({})
    print("Cleared emr_demo.patients and emr_demo.visits.\n")

    print(f"Creating demo patient {DEMO_PATIENT['_id']} ({DEMO_PATIENT['name']})...")
    patient_fields = {k: v for k, v in DEMO_PATIENT.items() if k != "_id"}
    upsert_patient(DEMO_PATIENT["_id"], patient_fields)
    print(f"  PHI fields encrypted at rest: national_id, dob, insurance_id\n")

    failures = 0
    print(f"Generating {len(SEED_VISITS)} seed visits...\n")
    for spec in SEED_VISITS:
        days_ago = spec["days_ago"]
        print(f"  [{spec['_id']}] {spec['specialty']} ({days_ago}d ago)...", end=" ", flush=True)
        try:
            body = generate_visit_body(spec)
        except Exception as e:
            print(f"Claude failed: {e}; using fallback.")
            failures += 1
            body = (
                f"Visit notes: {spec['chief_complaint']}. "
                f"Findings: {json.dumps(spec['structured'])}. "
                f"{spec['prose_hooks']}"
            )

        visit = {
            "_id": spec["_id"],
            "patient_id": DEMO_PATIENT["_id"],
            "visit_date": now_minus(days_ago),
            "specialty": spec["specialty"],
            "provider": spec["provider"],
            "chief_complaint": spec["chief_complaint"],
            "body": body,
            **spec["structured"],
        }
        try:
            upsert_visit(visit)
            print(f"saved ({len(body)} chars)")
        except Exception as e:
            print(f"upsert failed: {e}")
            failures += 1

    final_visits = db.visits.count_documents({})
    final_patients = db.patients.count_documents({})
    print(f"\nDone. {final_patients} patient(s), {final_visits} visit(s) in emr_demo.")
    print(f"Demo patient id: {DEMO_PATIENT['_id']}")
    if failures:
        print(f"WARN: {failures} step(s) hit a fallback or error.")
        sys.exit(2)


if __name__ == "__main__":
    main()
