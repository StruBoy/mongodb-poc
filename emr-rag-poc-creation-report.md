# EMR RAG PoC — Session Report

## Starting state
- `mongodb_poc/` had PoCs 1–5 fully deployed; PoC #6 didn't exist (no playbook, no directory)
- Plan was authored mid-session at `~/.claude/plans/review-the-current-project-steady-umbrella.md` — not in the project's `plans/` set, since the user's standing 5-PoC playbook stops at #5
- Atlas cluster `poc-cluster` already provisioned (M10, 8.3.2) and shared with the other PoCs; credentials reused from `secrets.txt` and the existing `.env` files
- Concept: apply the auto-embedding RAG pattern from `ai-kb/` to electronic medical records, with a **noticeable, plausible prognosis change** when follow-up visits get added — including one novel specialty the seed never used

## Design decisions made before coding

The user answered four shaping questions up front:
1. **Architectural emphasis:** equal weight on auto-updating summary (RAG) *and* polymorphic visit schemas (govtech-style) — both wins land in the same demo
2. **GP vs Patient view:** both have AI summary + Q&A chat; GP gets clinical terminology, Patient gets plain English with concrete next steps
3. **Add-visit UX:** pre-baked "Add follow-up visits" button drops a fixed 4-visit batch (demo determinism over flexibility)
4. **PHI encryption:** AES-256-GCM at the field level for `national_id`, `dob`, `insurance_id` — reuses the `govtech-casemgmt/src/crypto.py` pattern

Then a clinical research pass (3 web searches) grounded the demo arc in real literature: undiagnosed obstructive sleep apnoea as the upstream unifying cause of treatment-resistant anxiety, metformin-resistant prediabetes, and nocturnal arrhythmia — missed in up to 75% of women because they present with anxiety/insomnia rather than snoring. Formal name in the literature: "Syndrome Z" (metabolic syndrome + OSA). The plan was rewritten to bake the specific clinical numbers (HbA1c 6.2 → 6.4 → 6.7%, AHI 24, nadir SpO₂ 78%, CPAP 9 cmH₂O) into the seed and follow-up fixtures so the LLM summary would have something concrete to integrate.

## What got built

### Modules (`src/`)
| File | Purpose |
|---|---|
| `db.py` | Lazy-singleton `MongoClient` with `tlsCAFile=certifi.where()` — points at `emr_demo` |
| `embed.py` | `embed_texts(...)` via `voyage-3` (1024-dim) + `visit_to_text(visit)` (specialty + date + chief complaint + body, so semantic queries about specialty / time hit the embedding directly) |
| `crypto.py` | Ported from `govtech-casemgmt` verbatim except for the field set: `PHI_FIELDS = {"national_id", "dob", "insurance_id"}` |
| `patients.py` | `upsert_patient` (encrypts PHI on write), `get_patient` (decrypts on read), `list_patients`, `get_raw_patient` (no decrypt — for the inspector panel) |
| `visits.py` | **The architectural piece**: `upsert_visit` re-embeds via voyage-3 on every write — same path the seed and the add-follow-up button both use. Plus `get_visits_for_patient`, `add_followup_batch`, `remove_followup_batch` |
| `rag.py` | Persona-aware: `summarize_patient(pid, persona)` pulls all visits and asks Claude haiku 4.5 for a JSON `{summary, focus_areas[3]}`; `answer_question(pid, query, persona, k=4)` runs patient-scoped `$vectorSearch` (filter `{patient_id}`) and answers with persona-appropriate prompt. Both return retrieval/generation latencies |

### Scripts (`scripts/`, run via `python -m scripts.<name>`)
| Script | What it does |
|---|---|
| `check_env.py` | 7-stage check: env vars, MongoDB ping, `emr_demo` reachability, AES-256-GCM round-trip on `ENCRYPTION_KEY`, Voyage auth, Voyage billing probe (4 rapid calls), Anthropic auth |
| `create_db_index.py` | Creates `patients` + `visits` collections and `kb_visits_idx` (1024-dim cosine + `patient_id` + `specialty` filters); idempotent; polls until queryable |
| `verify_data.py` | 7 checks: patient present + PHI envelope round-trip, exactly 6 seed visits, required visit fields, 1024-dim embeddings, all 4 expected specialties, body-length floor (catches fallbacks), **no premature OSA/AHI/CPAP/polysomnography mentions in the seed**, vector index queryable |
| `smoke_test.py` | 6-step end-to-end test that proves the prognosis arc lands automatically (see "Smoke-test results" below) |

### Data pipeline (`data/`)
- `generate.py` — clears `emr_demo`, creates one demo patient (Maria Chen, 47F, BMI 31, encrypted PHI) and walks 6 `SEED_VISITS` fixtures across general practice / psychiatry / cardiology / endocrinology. Each fixture carries structured fields (vitals, scores, labs) plus prose hooks; Claude haiku 4.5 expands each into a 200–350-word note in the voice of the relevant specialty, with an explicit ban on mentioning OSA / sleep apnoea / AHI / CPAP / Syndrome Z (the seed must look fragmented). Persists through `src.visits.upsert_visit` so the seed and the editor share one write path. ~1 min total, ~USD 0.05.
- `followup_visits.py` — **hand-authored** 4-visit batch (no Claude call — demo determinism matters more than note variety): cardiology Holter follow-up (nocturnal PVC clustering + paroxysmal AF + 6.2s pause), endocrinology HbA1c → 6.7% with second-agent deferral pending OSA workup, psychiatry PHQ-9 regression with new morning headaches and witnessed apnoeas, **sleep_medicine** consult + polysomnography (AHI 24, nadir SpO₂ 78%, CPAP 9 cmH₂O, "Syndrome Z" diagnosis). The sleep_medicine visit uses fields (`ahi_per_hour`, `nadir_spo2_pct`, `rem_pct`, `slow_wave_sleep_pct`, `cpap_pressure_cmH2O`, `osa_severity`) that no other visit has — the polymorphism story.

### UI (`app.py`)
Single-pane Streamlit per patient:
- **Sidebar** — persona radio (`🩺 GP view` / `🧑 Patient view`), patient selector, **➕ Add follow-up visits** + **↺ Reset to seed** + 4 persona-specific demo prompts + clear-chat
- **Patient header** — name / age / BMI / primary GP, plus a collapsible **PHI panel** that decrypts national_id / dob / insurance_id from their AES-256-GCM envelopes for inspection
- **📋 AI overview card** — 3–5 sentence summary + 3 focus-area bullets + retrieval/generation latency footer; **🔄 Refresh summary** button; cached per (patient, persona) so toggles don't re-spend tokens unless asked
- **🗂️ Visit timeline** — chronological (newest first), expandable per visit, color-coded by specialty (🩺 GP, 🧠 psych, ❤️ cardio, 🧪 endo, 😴 sleep med); each card shows full body + structured fields JSON
- **💬 Q&A chat** — patient-scoped `$vectorSearch`, sources expander per turn shows visit ID / date / specialty / similarity score
- Both `➕ Add follow-up visits` and `↺ Reset to seed` invalidate the summary cache so the prognosis shift is visible without an extra click

### Documentation
- Updated root `README.md`: extended the status table to 6 PoCs, updated prerequisites for PoC 6 (Voyage + Anthropic + ENCRYPTION_KEY), added the 8-step "Deploy the EMR RAG PoC" walkthrough + 7-beat demo flow + project-layout entry
- Wrote the source plan to `~/.claude/plans/review-the-current-project-steady-umbrella.md` (not in `mongodb_poc/plans/` — the user's standing playbook set stops at #5)

## Problems we hit and fixed

This session was largely on-rails — most of the engineering was a port from `ai-kb/` (RAG pipeline) and `govtech-casemgmt/` (AES-GCM encryption + polymorphic schema). The real items:

| Symptom | Root cause | Fix |
|---|---|---|
| Permission deny under `/Users/steven/projects/mongodb_poc/emr-rag/` blocked both `mkdir` and `Write` even after granting in the prompt | The project's `.claude/settings.json` had `Write(/**)` in deny list which apparently took precedence over the `Write(/Users/steven/projects/mongodb_poc/**)` allow | User edited `.claude/settings.local.json` to relax the rule; afterwards every Write succeeded. Created the directory tree manually first, then file writes worked |
| The BEFORE summary mentioned "consider a sleep study" as a focus area — risked weakening the diagnostic-pivot moment | LLM is medically literate; given an unrefreshing-sleep complaint, it'll suggest the workup even without OSA in evidence. This is actually clinically realistic, not a bug | Decided to keep it. The smoke test asserts the BEFORE summary contains no AHI / CPAP / polysomnography (i.e. no confident diagnosis), but allows the suggestion. The AFTER summary's prognosis shift — from "fragmented, not converging" to "unified, treatable, expect measurable improvement" — is dramatic enough on its own. Confirmed by running both side-by-side |
| Couldn't directly verify the Streamlit UI from the headless tool environment — `streamlit run` works but I can't open a browser | Streamlit catches and displays exceptions in the UI rather than crashing the process, so a working healthcheck doesn't prove the app renders cleanly | Triangulated: `py_compile` passes, every imported symbol from `src.*` resolves at module level, healthcheck returns 200, no errors in the streamlit log after the page is hit. User will visually verify when they run the demo |

## Smoke-test results (final corpus, threshold-free)

All 6 steps pass in a single run. Most importantly, the BEFORE / AFTER prognosis shift lands automatically with no prompt tweaking required:

**BEFORE summary (clinical persona):**
> 47-year-old woman with newly diagnosed prediabetes (HbA1c 6.4% [vis-005]), generalized anxiety disorder with comorbid insomnia, and benign palpitations secondary to anxiety. Initial presentation [vis-001] showed fatigue, weight gain, and metabolic risk; investigations confirmed impaired fasting glucose and mild dyslipidaemia. Trajectory has worsened: HbA1c progressed from 6.2% to 6.4% despite lifestyle efforts [vis-005], prompting initiation of metformin. Psychiatry intervention begun [vis-003] with sertraline and CBT-I; anxiety improved (GAD-7 13→11) but sleep remains refractory despite dose escalation to 100 mg and addition of trazodone [vis-006]. Cardiology evaluation [vis-004] excluded structural cardiac disease; palpitations attributed to anxiety with occasional benign PVCs. Current clinical picture dominated by poor sleep quality limiting metabolic intervention adherence — addressing sleep disturbance is critical to improving overall trajectory.

**AFTER summary (clinical persona, same patient, +4 visits):**
> This 47-year-old woman presented with fatigue and weight gain [vis-001], leading to diagnosis of prediabetes (HbA1c 6.2%) and new-onset generalised anxiety disorder with insomnia [vis-002]. Despite sertraline escalation to 100mg and addition of trazodone, plus initiation of metformin for worsening glycaemic control (HbA1c 6.7%, now meeting T2DM criteria) [vis-102], she remained treatment-resistant with progressive mood deterioration and unexplained morning headaches [vis-103]. Cardiology workup for palpitations revealed nocturnal arrhythmia clustering (PVCs, paroxysmal AF, sinus pause) atypical for anxiety [vis-101], prompting sleep evaluation. Polysomnography confirmed moderate-severe obstructive sleep apnoea (AHI 24, REM-predominant, nadir SpO2 78%) [vis-104] — a unifying diagnosis explaining the refractory psychiatric symptoms, metformin-resistant hyperglycaemia, and nocturnal arrhythmias. CPAP therapy commenced at 9 cmH2O with excellent titration response (AHI reduced to 2.1). This diagnosis fundamentally reframes the clinical picture: OSA rather than primary psychiatric or metabolic disease has been driving the downstream pathology across multiple organ systems.

| Step | Question / action | Result | Top source | Score |
|---|---|---|---|---|
| 1 | Summary BEFORE follow-ups | No AHI / CPAP / polysomnography mentioned (no confident diagnosis) | — | — |
| 2 | "What's driving her poor sleep?" BEFORE | Hedged answer; cites sleep architecture but no diagnosis | `vis-006` (psychiatry) | 0.7567 |
| 3 | `add_followup_batch` | 4 visits inserted (1 sleep_medicine — novel specialty); no schema migration, no re-embed pipeline | — | — |
| 4 | Summary AFTER follow-ups | Names OSA explicitly, cites AHI + polysomnography + CPAP, frames OSA as **unifying** upstream cause | — | — |
| 5 | "What is her CPAP plan?" | Sleep_medicine visit is top source — proves novel specialty was indexed end-to-end | `vis-104` (sleep_medicine) | 0.6893 |
| 6 | "What's driving her poor sleep?" AFTER | Sleep_medicine visit now in sources; answer is unambiguous OSA diagnosis | `vis-103` (psychiatry) + `vis-104` (sleep_medicine) | 0.7567 / 0.7123 |

End-to-end retrieval averaged 30–250 ms; generation 5–8 s per Claude haiku 4.5 call.

## Deviations from the plan

Minimal — the plan held up well after the mid-session clinical-research pass tightened it.

- **`add_followup_batch` auto-invalidates the summary cache** in the UI so the user doesn't have to click "Refresh summary" after adding visits — the prognosis shift lands in one click, not two
- **Added `↺ Reset to seed` button** alongside `➕ Add follow-up visits` for quick demo re-runs without re-running `data.generate`
- **`smoke_test.py` cleans up the follow-up batch** in both pass and fail paths via a `fail()` helper — leaves the corpus clean regardless
- **`get_raw_patient()` exists** in `src/patients.py` even though the inspector view is folded into the patient header expander — left in case you want a separate inspector page later
- **The seed psychiatry note can mention "unrefreshing sleep" and "early-morning awakening"** but not snoring or witnessed apnoeas (the latter only appear in the follow-up psych note); kept the line at non-specific symptoms vs OSA-suggestive symptoms

## Final layout
```
emr-rag/
├── app.py                  ← Streamlit (GP / Patient persona toggle)
├── requirements.txt
├── .env / .env.example
├── src/
│   ├── db.py
│   ├── embed.py
│   ├── crypto.py           ← AES-256-GCM (PHI fields)
│   ├── patients.py
│   ├── visits.py           ← upsert_visit re-embeds on every write
│   └── rag.py              ← persona-aware summary + Q&A
├── scripts/
│   ├── check_env.py
│   ├── create_db_index.py
│   ├── verify_data.py
│   └── smoke_test.py
└── data/
    ├── generate.py         ← seed via Claude (6 fragmented visits)
    └── followup_visits.py  ← hand-authored 4-visit demo batch
```

## Run book (recap)
```bash
cd emr-rag && source venv/bin/activate
python -m scripts.check_env         # 7/7 OK
python -m scripts.create_db_index   # idempotent
python -m data.generate             # ~1 min, ~USD 0.05
python -m scripts.verify_data       # 7 checks pass
python -m scripts.smoke_test        # 6 steps pass; prognosis arc lands
streamlit run app.py                # localhost:8501
```

## Outstanding
- Multi-patient browsing — the patient dropdown is wired but the seed only generates `pat-001`. Add more patients to `data/generate.py:DEMO_PATIENT` (or extend to a list) if you want to demo browsing across patients
- The `specialty` filter on `retrieve_visits` is plumbed but the UI doesn't expose it; a specialty filter next to the chat input would land the "filtered semantic search" point if asked
- Atlas Vector Search's **native auto-embedding via Voyage AI** (preview) is the production-style alternative to the application-side pattern used here — worth flagging in the demo but not switching to (the application pattern is more portable across MongoDB versions and the embed call is right there in `src/visits.py`)
- The clinical narrative is very specifically tuned to the OSA-as-unifying-diagnosis arc; if the audience is sleep-medicine specialists they may push back on edge cases (e.g. AHI 24 is technically *moderate* not "moderate-severe" by AASM 2017 — the visit body uses the "moderate-severe" descriptor because the REM AHI is 38 and clinical practice often treats based on REM AHI when it's substantially higher than overall AHI). Have the literature framing ("Syndrome Z", the underdiagnosis-in-women angle) ready as backup
- PoCs 1–6 are now all implemented; the playbook set in `mongodb_poc/plans/` only documents 1–5 (the EMR plan was authored as a personal plan under `~/.claude/plans/`). Worth promoting to `plans/06_Healthcare_EMR_RAG.md` if this PoC is going to be presented externally
