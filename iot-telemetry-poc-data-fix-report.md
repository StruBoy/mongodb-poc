# IoT-Telemetry PoC — Data-Fix Session Report

## Starting state

The fleet generator seeded 1,000 cell towers across three Singapore districts with intentionally imbalanced counts:

| District | Count |
|---|---|
| West    | 350 |
| Central | 350 |
| East    | 300 |

For the demo we wanted each district to carry roughly equal representation so per-region aggregates and the dashboard regional view weren't visually skewed by raw count differences. 1,000 ÷ 3 leaves a remainder of 1, so an exact even split is impossible without dropping the total.

## What changed

### `iot-telemetry/data/generate_fleet.py`

- `DISTRICTS["West"].count`: 350 → 333
- `DISTRICTS["Central"].count`: 350 → 334  (Central absorbs the remainder)
- `DISTRICTS["East"].count`: 300 → 333
- Docstring updated: `"counts are 333 / 333 / 334 (West / East / Central) to match scripts/verify_data.py"`

Total fleet size is unchanged at 1,000. The polygon boundaries, rejection-sampling logic, tower ID format, `random.seed(42)`, and the per-district `small_cell_ratio` values (West 0.25, Central 0.45, East 0.20) were left untouched — those reflect intentional density choices (CBD has the most small cells) that are independent of tower count.

### `iot-telemetry/scripts/verify_data.py`

- `EXPECTED_DISTRICTS = {"Central": 334, "East": 333, "West": 333}`  (was `{"Central": 350, "East": 300, "West": 350}`)
- `EXPECTED_FLEET_COUNT = 1000` unchanged.

## Files intentionally untouched

- `data/stream_telemetry.py` — reads `region` and `tower_id` from tower documents at runtime; agnostic to per-district counts.
- `app.py` — keys regional colouring by region name, not count.
- `src/analytics.py` — aggregates by `meta.region`; no hardcoded counts.
- `scripts/create_db_index.py`, `scripts/smoke_test.py`, `scripts/check_env.py` — no district-count assumptions.

## Verification

Regenerated the fleet and reran verification:

```
$ python -m data.generate_fleet
Inserted 1000 towers across 3 distinct Singapore zones:
  West     (WST):  333 towers (254 macro,  79 small_cell)  lat[1.282–1.460] lon[103.640–103.824]
  Central  (CEN):  334 towers (185 macro, 149 small_cell)  lat[1.273–1.455] lon[103.798–103.936]
  East     (EST):  333 towers (263 macro,  70 small_cell)  lat[1.294–1.425] lon[103.885–104.019]

$ python -m scripts.verify_data
1. Tower fleet count
   [OK] 1000 towers (expected 1000)
2. District distribution
   [OK] Central: 334 (expected 334)
   [OK] East:    333 (expected 333)
   [OK] West:    333 (expected 333)
3. Tower ID prefix matches region                  [OK]
4. Tower required fields                           [OK]
5. Tower types                                     [OK]
6. Telemetry time-series options                   [OK]
7. Telemetry document shape                        [OK]
=== Data verification passed. ===
```

The macro/small-cell mix per district remains anchored to each district's `small_cell_ratio` (West ~24%, Central ~45%, East ~21% small cells in the regen above), so the deliberate density story per region is preserved.

## Files modified

```
iot-telemetry/
├── data/generate_fleet.py     ← count 350/350/300 → 333/334/333; docstring updated
└── scripts/verify_data.py     ← EXPECTED_DISTRICTS values updated
```

## Verification run book

```bash
cd iot-telemetry && source venv/bin/activate
python -m data.generate_fleet     # prints per-district counts ~333/333/334
python -m scripts.verify_data     # all OK except the telemetry-recency WARN if streamer is off
```
