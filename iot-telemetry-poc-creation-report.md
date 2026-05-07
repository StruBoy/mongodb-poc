# IoT-Telemetry PoC — Session Report

## Starting state
- `mongodb_poc/` had `hybrid-search/`, `fraud-detect/`, and `ai-kb/` fully deployed; `iot-telemetry/` was an empty directory
- Playbook `plans/05_Telco_IoT_Telemetry.md` describes 1,000 cell towers across three APAC regions (Sydney, Singapore, Mumbai) streaming telemetry into a time-series collection at ~1,000 events/sec, with a Streamlit dashboard showing regional health, throughput timeline, degraded towers, and failure injection
- Atlas cluster `poc-cluster` already provisioned (M10, 8.3.2) and shared with the other three PoCs; credentials in `secrets.txt` and reused `.env` values
- User asked for two upfront deviations from the playbook:
  - Failure injection via a `control_failures` collection instead of `/tmp/telemetry_failures.txt` (cross-platform, reinforces the "operational state lives in MongoDB" story)
  - Idempotent `create_db_index.py` that detects an existing time-series collection and skips rather than re-creating (since time-series options can't be modified after creation)

## What got built

### Modules (`src/`)
| File | Purpose |
|---|---|
| `db.py` | Lazy-singleton `MongoClient` + `AsyncIOMotorClient` with `tlsCAFile=certifi.where()` (sync for dashboard/scripts/analytics, async for the streamer). Points at `telco_demo`. |
| `analytics.py` | Four aggregation pipelines: `regional_health` (avg metrics per district over a window), `degraded_towers` (towers above a packet-loss threshold), `throughput_timeline` ($dateTrunc-bucketed series for the chart), `write_throughput_estimate` (live ops/sec) |

### Scripts (`scripts/`, run via `python -m scripts.<name>`)
| Script | What it does |
|---|---|
| `check_env.py` | 3-stage check: env vars, MongoDB ping, `telco_demo` db / collection presence. No Voyage / Anthropic — this PoC is pure time-series + aggregations |
| `create_db_index.py` | Phase 1.3 (db + telemetry time-series + towers + control_failures collections) and Phase 1.4 (compound indexes on `meta.tower_id+ts` and `meta.region+ts`); idempotent; detects time-series option drift and fails loudly rather than silently mismatching |
| `verify_data.py` | 8 checks: tower count, district distribution, ID prefix matches region, required fields, tower types, time-series collection options, telemetry document shape, telemetry recency |
| `smoke_test.py` | End-to-end pipeline test: confirms stream is alive, runs all four analytics queries, injects 3 failures into West, waits 8s, confirms those towers cross the 5%-packet-loss threshold, cleans up |

### Data pipeline (`data/`)
- `generate_fleet.py` — 1,000 towers across three Singapore zones (350 Central / 300 East / 350 West), district-prefixed IDs (`TWR-CEN-####` / `TWR-EST-####` / `TWR-WST-####`); rejection-sampled inside per-district polygons (see Tower distribution evolution below)
- `stream_telemetry.py` — async motor-based streamer pumping ~1,000 events/sec across the fleet; reads `control_failures` each cycle to know which towers should emit degraded metrics; `--rate` flag overrides the default

### UI (`app.py`)
Streamlit dashboard with:
- **Top metrics** — live write rate, towers reporting, district count, active failures
- **District health (last 30s)** — one card per Singapore district with avg signal / packet loss / throughput / temperature; red badge above 3% packet loss
- **Throughput timeline** — per-district line chart over last 2 minutes
- **Tower fleet map** — Plotly scatter on a Singapore base map, recoloured red as failures are injected
- **Towers needing attention** — expandable list of towers above 5% packet loss
- **Sidebar** — per-district inject buttons (Central / East / West, 3 each), `🔥 Major incident` (15 in West), `✅ Clear all failures`, plus a live "Active failures" panel showing currently-failing tower IDs

### Documentation
- Updated root `README.md`: flipped status to **Implemented**, added 9-step deploy walkthrough + 6-beat demo flow + project-layout entry, updated prerequisites note to call out that this PoC needs neither Voyage nor Anthropic

## Tower distribution evolution

The fleet generator went through three explicit iterations, each driven by a user request after seeing the previous result on the dashboard map:

| Iteration | Approach | What looked wrong | User ask |
|---|---|---|---|
| **v1** | Three Gaussian clusters around Marina Bay / Tampines / Jurong East | Three blobs with empty corridors between them; some samples in water | "Space them throughout Singapore" |
| **v2** | Single Singapore mainland polygon (16-vertex outline) + uniform sampling within bbox + rejection; districts assigned by longitude cuts (`< 103.80 / 103.80–103.90 / > 103.90`) | Coverage was uniform, but each district spanned the full N–S extent of the island — so on the map, towers from all 3 districts intermingled vertically | "Three separate zones with limited overlap" |
| **v3** | Three explicit district polygons that tile mainland Singapore, sharing edges along two inland partition lines (Sembawang ↔ Pasir Panjang for W↔C, Sengkang ↔ East Coast Park for C↔E). Each district is its own contiguous geographic blob | — | (final) |

Result on the v3 design (verified by spot-checking 13 well-known SG neighborhoods): 12/13 land in their natural district, with Hougang the one borderline case (officially North-East, equidistant between Bishan and Tampines).

## Problems we hit and fixed

The session split into two distinct halves: a clean playbook implementation, then a deep dive into Streamlit's UI reconciliation when the user reported persistent sidebar staleness after Clear.

| Symptom | Root cause | Fix |
|---|---|---|
| AppTest's `use_container_width` deprecation warnings cluttering output | Streamlit 1.57 has flagged the kwarg for removal | Switched all `use_container_width=True` → `width="stretch"` |
| `verify_data.py` warning that telemetry was empty when streamer hadn't been started | Expected — soft check, not a hard failure | Phrased as `[WARN]` rather than `[FAIL]` so fleet-only verification still passes 0 |
| User saw stale "Active failures" sidebar list AND stale "Towers needing attention" cards after clicking Clear | Two issues: (1) Streamlit's reconciler orphans DOM elements when a script position toggles between rendering N elements and 0 elements; (2) the bottom degraded list reads telemetry over a 30-second window, so for the first ~30s after Clear, the running average still reflected the recent degraded readings | Iterated through five fix attempts (see below) before landing on the working architecture |

### The Streamlit reconciliation rabbit hole (5 iterations)

| Attempt | What was tried | Why it failed |
|---|---|---|
| 1 | Remove `st.rerun()` from click handlers, let the same script run render the new state | Bottom degraded list still had stale cards (running-average lag); sidebar still showed orphans because the variable-count `if cond: render N elements` block never gives the diff a stable element to anchor on |
| 2 | Wrap conditional sidebar block in `with st.sidebar.empty().container():` (placeholder pattern) | Worked in `AppTest` but not live Streamlit. The placeholder's container was created/destroyed conditionally — same orphan class as before |
| 3 | Single placeholder method-call pattern: `failures_slot.warning(body)` or `failures_slot.empty()` | Bottom section finally cleared cleanly, but sidebar still ghosted — `st.sidebar.empty()` doesn't reconcile children the same way as main-page `st.empty()` |
| 4 | Always-rendered single `st.sidebar.markdown(html, unsafe_allow_html=True)` with HTML body that's either the rendered failures box or `""` | Element count was finally stable across runs, but orphans pinned in the live DOM **from previous code versions** persisted — watchdog hot-reloads Python but the WebSocket session keeps the old DOM tree |
| 5 (final) | Move all live data into `@st.fragment(run_every=3)`, remove the `time.sleep(refresh_seconds) + st.rerun()` auto-refresh entirely | Click handlers now trigger a clean main-script rerun; live data refreshes on the fragment's own timer. No more rapid-fire whole-page reruns, no more diff overload. Required users to fully restart streamlit + hard-refresh the browser to clear orphans pinned by earlier versions |

### Bonus problem also fixed in iteration 5
- A user screenshot mid-rabbit-hole showed **duplicated "District health" cards** rendered both above and below the throughput chart. Same root cause: rapid-fire whole-page reruns colliding with watchdog code reloads, leaving orphans in arbitrary positions. The fragment refactor fixed this for free.

## Final smoke-test results
| Step | Outcome |
|---|---|
| Stream alive | ~1,000 ops/sec sustained on M10 (~150ms per 1,000-event cycle) |
| Regional health | All 3 districts reporting; baseline ~0.5% packet loss, ~450 Mbps throughput, 38°C |
| Throughput timeline | 60 (region, second) buckets per minute × 3 regions |
| Inject 3 failures in West | Visible within 8s on degraded list and on the map; West district packet loss visibly elevated |
| Clear all failures | Sidebar empties immediately, degraded list clears within ~10s, district health returns to nominal |

## Deviations from the playbook
- **Failure injection via `control_failures` collection** instead of `/tmp/telemetry_failures.txt` (approved upfront)
- **Idempotent `create_db_index.py` with time-series option drift detection** instead of recreate-and-hope (approved upfront)
- **Singapore-only fleet** (3 districts: Central / East / West) instead of three APAC cities (Sydney / Singapore / Mumbai). User wanted the demo to feel relatable — single recognizable city
- **District polygons that tile Singapore** instead of cluster-around-center, after two iterations on tower distribution
- **`@st.fragment(run_every=3)` for live data** instead of `time.sleep + st.rerun` auto-refresh. The playbook's pattern was the textbook Streamlit approach circa 2023; the fragment API (1.32+) is the right tool for periodic refresh and avoids the reconciliation problems documented in the rabbit hole above
- **Refresh-interval slider removed** as a side effect of the fragment refactor (`run_every` is set at decoration time; making it dynamic would require session-state plumbing that didn't pull its weight)

## Final layout
```
iot-telemetry/
├── app.py                 ← Streamlit dashboard (fragment-based live data)
├── requirements.txt       ← + watchdog (added by Streamlit's "for better performance" suggestion)
├── .env / .env.example
├── src/
│   ├── db.py              ← sync + async clients
│   └── analytics.py       ← 4 aggregation pipelines
├── scripts/
│   ├── check_env.py
│   ├── create_db_index.py
│   ├── verify_data.py
│   └── smoke_test.py
└── data/
    ├── generate_fleet.py  ← 3-polygon district partition over Singapore mainland
    └── stream_telemetry.py ← async motor streamer
```

## Run book (recap)
```bash
cd iot-telemetry && source venv/bin/activate
python -m scripts.check_env         # 3/3 OK
python -m scripts.create_db_index   # idempotent
python -m data.generate_fleet       # 1,000 towers, ~1s
# in terminal 2:
python -m data.stream_telemetry     # ~1,000 events/sec, leave running
# back in terminal 1:
python -m scripts.verify_data       # 8 checks
python -m scripts.smoke_test        # 7-step end-to-end
streamlit run app.py                # localhost:8501
```

## Outstanding
- The `time.sleep + st.rerun` → `st.fragment` refactor pattern is worth back-porting to `fraud-detect/` and `ai-kb/` if either ever exhibits similar reconciliation issues. Neither has been reported, so no action taken yet
- The W↔C and C↔E district partition lines were tuned to keep most well-known SG neighborhoods in their "natural" district. Hougang specifically lands in Central rather than East (it's officially North-East planning region). If a demo audience flags this, nudge the C↔E partition's southern endpoint slightly west
- Outlying islands (Sentosa, Pulau Ubin, Pulau Tekong, Jurong Island) are deliberately excluded from the polygon — they're not part of the demo. Adding them would mean a multi-polygon Singapore boundary
- The remaining PoC (`govtech-casemgmt/`) is still an empty scaffold with its playbook ready under `plans/`
