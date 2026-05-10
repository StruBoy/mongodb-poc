"""The canned 4-visit follow-up batch dropped in by the demo's "Add follow-up" button.

These are hand-authored (no Claude call) for demo determinism — the prognosis
shift only lands if the four notes contain the right clinical content. Each
visit goes through src.visits.upsert_visit so each is auto-embedded.

The arc:
  vis-101 — Cardiology Holter result reveals nocturnal PVC clustering and one
            paroxysmal AF episode. Atypical for anxiety-driven arrhythmia.
            First time anyone recommends a sleep study.
  vis-102 — Endocrinology HbA1c climbs to 6.7% despite adherence. Endo defers
            second oral agent pending the OSA workup.
  vis-103 — Psychiatry: PHQ-9 regression. New morning headaches. Partner
            reports witnessed apneas. Sleep medicine referral.
  vis-104 — Sleep medicine + polysomnography: AHI 24 (moderate-severe OSA),
            nadir SpO2 78%, CPAP started. **The unifying diagnosis.** This is
            the novel specialty — uses fields no other visit has (ahi_per_hour,
            nadir_spo2_pct, cpap_pressure_cmH2O, etc.).

`days_ago` is computed at insert time relative to "now", so the visits stay
recent regardless of when the demo runs.
"""
from datetime import datetime, timedelta, timezone


def _now_minus(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


FOLLOWUP_VISITS = [
    {
        "_id": "vis-101",
        "visit_date": _now_minus(21),
        "specialty": "cardiology",
        "provider": "Dr. Lim Han Wei, Cardiologist",
        "chief_complaint": "24-hour Holter monitor results review",
        "body": (
            "Holter follow-up. Patient returned for review of the 24-hour ambulatory ECG ordered at the "
            "previous visit. Recording was technically adequate (>22h analysable). Findings: predominantly "
            "sinus rhythm at a mean rate of 71 bpm. PVC burden 412 over the recording period (0.5%), but "
            "with marked temporal clustering — 78% of PVCs occurred between 02:00 and 05:00, with multiple "
            "couplets and one short run of non-sustained ventricular tachycardia (4 beats, rate 142). Of "
            "particular note, a single episode of paroxysmal atrial fibrillation lasting 6 minutes was "
            "captured at 04:12, terminating spontaneously, and one 6.2-second sinus pause at 03:48. "
            "This nocturnal predominance is atypical for arrhythmia driven primarily by anxiety, which "
            "would more typically distribute throughout the day with stress correlation. The pattern — "
            "nocturnal PVC clustering, paroxysmal AF in sleep, and a sinus pause — raises strong suspicion "
            "for a sleep-related breathing disorder driving autonomic instability overnight. BP today "
            "132/82, HR 76. Assessment: nocturnal arrhythmia of likely non-cardiac origin. Plan: refer to "
            "sleep medicine for polysomnography before considering any anti-arrhythmic. Have asked GP to "
            "ask about snoring and witnessed apnoeas. Will hold on rate-control or rhythm-control therapy "
            "pending sleep workup."
        ),
        "bp_systolic": 132,
        "bp_diastolic": 82,
        "heart_rate_bpm": 76,
        "holter_findings": (
            "Nocturnal PVC clustering 02:00-05:00 (78% of total burden); 1x paroxysmal AF 6 min at 04:12; "
            "1x sinus pause 6.2s at 03:48; 1x NSVT (4 beats)."
        ),
        "nocturnal_pvc_count": 321,
        "paroxysmal_af_episodes": 1,
        "longest_pause_seconds": 6.2,
        "sleep_study_recommended": True,
    },
    {
        "_id": "vis-102",
        "visit_date": _now_minus(14),
        "specialty": "endocrinology",
        "provider": "Dr. Rahim Abdullah, Endocrinologist",
        "chief_complaint": "Endocrinology follow-up; HbA1c trending despite metformin",
        "body": (
            "Endocrinology follow-up at 3 months post metformin initiation. Patient reports good adherence "
            "to metformin (now at 1g BID, well tolerated, no GI effects). Continues with dietary measures "
            "to the best of her ability. Despite this, HbA1c has climbed from 6.4% to 6.7% — now meeting "
            "ADA criteria for type 2 diabetes mellitus on a single value (would need confirmation but the "
            "trajectory is clear). Fasting glucose 6.8 mmol/L. Weight stable at 80kg, no clinical evidence "
            "of secondary causes (TSH was normal at baseline; cortisol not indicated). The degree of "
            "insulin resistance appears disproportionate to her BMI, and the failure of optimised "
            "metformin to even flatten the trajectory is unusual. I am aware of the cardiology referral "
            "to sleep medicine for nocturnal arrhythmia. I will hold off on adding a second oral agent "
            "(SGLT2 or GLP-1) until the sleep medicine workup completes, given that untreated obstructive "
            "sleep apnoea is well-recognised as a driver of insulin resistance and may explain the "
            "current picture. If OSA is confirmed and treated, glycaemic trajectory may flatten without "
            "additional pharmacotherapy. If sleep workup is negative, will escalate at the next visit. "
            "Recheck HbA1c in 3 months. Continue metformin 1g BID. Statin discussion deferred again."
        ),
        "hba1c_pct": 6.7,
        "fasting_glucose_mmol": 6.8,
        "weight_kg": 80.0,
        "bmi": 30.5,
        "metformin_dose_mg_daily": 2000,
        "second_agent_deferred": True,
        "deferral_reason": "Awaiting sleep medicine workup for suspected OSA",
    },
    {
        "_id": "vis-103",
        "visit_date": _now_minus(10),
        "specialty": "psychiatry",
        "provider": "Dr. Wong Mei Ling, Psychiatrist",
        "chief_complaint": "Psychiatry follow-up; symptom regression, new physical complaints",
        "body": (
            "Psychiatry follow-up at 6 months. Patient reports worsening over the last 4-6 weeks despite "
            "continued sertraline 100mg daily and trazodone 50mg at night. PHQ-9 12 (up from 9), GAD-7 14 "
            "(up from 11). On structured questioning, she now reports two new symptoms that have emerged "
            "or been clarified since the last visit: (1) morning headaches, dull and bifrontal, present "
            "on most mornings, resolving by mid-morning; and (2) on direct questioning, her partner has "
            "reported witnessed pauses in her breathing during sleep, with loud snoring — Maria had "
            "previously not mentioned this, considering it unrelated. She is also aware of the cardiology "
            "Holter findings and is anxious about the AF episode. The sertraline is at maximum effective "
            "dose for her presentation; further escalation is unlikely to address residual symptoms "
            "particularly given that her clinical picture is increasingly consistent with sleep-disordered "
            "breathing as a primary driver of mood, sleep architecture disruption, and morning headaches. "
            "Plan: urgent referral to sleep medicine (cardiology has already initiated this pathway). Hold "
            "current psychotropic regimen rather than escalating. Reviewed with patient that her anxiety "
            "and mood are likely partly downstream of sleep fragmentation and that we may see meaningful "
            "improvement with treatment of the underlying sleep disorder. Review in 6-8 weeks post sleep "
            "intervention."
        ),
        "phq9_score": 12,
        "gad7_score": 14,
        "morning_headaches": True,
        "witnessed_apneas": True,
        "snoring_reported": True,
        "sleep_medicine_referral": True,
    },
    {
        "_id": "vis-104",
        "visit_date": _now_minus(4),
        "specialty": "sleep_medicine",
        "provider": "Dr. Alicia Yeo, Sleep Medicine",
        "chief_complaint": "Polysomnography and CPAP titration; suspected obstructive sleep apnoea",
        "body": (
            "Sleep medicine consultation and split-night attended polysomnography. Patient referred from "
            "cardiology and psychiatry with a constellation of treatment-resistant anxiety with "
            "unrefreshing sleep, morning headaches, witnessed apnoeas reported by partner, nocturnal "
            "PVC clustering with one paroxysmal AF episode on Holter, and metformin-resistant prediabetes "
            "trending toward T2DM. STOP-BANG pre-test 5/8 (high probability). Diagnostic portion of the "
            "PSG demonstrated severe sleep-disordered breathing: AHI 24 events/hour overall (moderate-severe "
            "OSA), predominantly obstructive (OAI 19, CAI 1, mixed 4), with REM-predominant clustering "
            "(REM AHI 38). Nadir SpO2 78%, with 14% of total sleep time below 90%. Sleep architecture "
            "markedly fragmented: total sleep time 5h12m, sleep efficiency 71%, 0% slow-wave (N3) sleep, "
            "REM reduced at 11%. Arousal index 32/hour. Titration portion: optimal CPAP pressure 9 "
            "cmH2O, abolishing residual events to AHI 2.1, with restoration of 8% N3 sleep and "
            "improvement of nadir SpO2 to 91%. Diagnosis: moderate-severe obstructive sleep apnoea — "
            "this picture meets criteria for what the literature terms 'syndrome Z' (OSA superimposed "
            "on metabolic syndrome) and very plausibly accounts for the unifying upstream driver of her "
            "treatment-resistant anxiety, the metformin-resistant glycaemic decline, and the nocturnal "
            "arrhythmia documented on Holter. CPAP commenced this week with adherence-monitoring telemetry. "
            "Plan: follow up in 4 weeks for adherence review; expect measurable improvement in BP, "
            "PHQ-9, and arrhythmia burden within 12 weeks if adherence ≥4 hours per night; HbA1c "
            "trajectory likely to flatten over 3-6 months. Communicating with cardiology, endocrinology, "
            "and psychiatry — recommend deferring any escalation in their respective regimens until "
            "the effect of OSA treatment can be assessed at 3 months."
        ),
        "ahi_per_hour": 24.0,
        "rem_ahi": 38.0,
        "nadir_spo2_pct": 78,
        "time_below_90_spo2_pct": 14.0,
        "sleep_efficiency_pct": 71,
        "rem_pct": 11,
        "slow_wave_sleep_pct": 0,
        "arousal_index_per_hour": 32,
        "stop_bang_score": 5,
        "cpap_pressure_cmH2O": 9,
        "cpap_residual_ahi": 2.1,
        "osa_severity": "moderate-severe",
        "diagnosis_unifying": True,
    },
]
