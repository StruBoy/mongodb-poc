# Data Residency PoC — Session Report

## Starting state
- `mongodb_poc/` had PoCs 1–6 implemented; PoC #7 didn't exist (no playbook, no directory)
- Plan authored mid-session at `~/.claude/plans/read-the-files-in-valiant-lobster.md` — not in the project's `plans/` set (same precedent as PoC #6)
- No Atlas Global Cluster yet — would need to be provisioned mid-session (M30+ multi-region, distinct from the M10s the other PoCs use)
- Concept: demonstrate MongoDB Atlas Global Cluster zone sharding for tenant data residency. 100 customers start in one region, get migrated to natural regions (US/EU/APAC), then individual customers can be overridden to any zone. The audience watches data physically move between shards.

## Design decisions made before coding

The user answered four shaping questions up front:
1. **Sharding mode:** real Atlas Global Cluster — not simulated. Three physical M30 replica sets in different regions, real zone sharding, real cross-shard document moves. ~USD 24/day while running, must be paused between sessions.
2. **Regions and clouds:** Global (US/EU/APAC), not APAC-internal. **Multi-cloud:** US zone on AWS `us-east-1` (Virginia), EU zone on Azure `germanywestcentral` (Frankfurt), APAC zone on GCP `asia-southeast1` (Singapore). The cross-cloud setup strengthens the demo — the residency contract holds across cloud providers, not just across regions of one provider, with the same single connection string and unchanged application code.
3. **Customer data shape:** orders/transactions, ~50–100 per customer (~7,500 records total). Small enough to migrate quickly, big enough to make per-shard counts visible.
4. **Migration UX:** async with polling status — but this evolved significantly during the session (see "Problems we hit").

Two follow-up questions tightened the demo flow:
5. **Initial state:** all 100 customers start in US zone; one click bulk-migrates to natural regions, then individual overrides land afterward.
6. **Map technology:** Plotly `scatter_map` (Mapbox tiles); two-marker layout per customer (business location + data centre) with connecting lines coloured by zone.
7. **Verification:** Shard-distribution inspector panel — proves data physically moved via `explain.executionStats`.

## What got built

### Modules (`src/`)
| File | Purpose |
|---|---|
| `db.py` | Lazy-singleton `MongoClient` with `tlsCAFile=certifi.where()` — points at `residency_demo` |
| `zones.py` | `ZONE_NAMES` (US/EU/APAC labels), `ZONE_REGION_INFO` (data-centre city/lat/lon/cloud provider/cloud region), full `ZONE_FOR_COUNTRY` map (~45 ISO codes), `ZONE_REPRESENTATIVE_CODE` (used for cross-zone overrides), helpers `zone_for_country()`, `representative_code_for_zone()` |
| `customers.py` | `list_customers()` (adds `data_zone` field from the location code), `get_customer()`, `customer_count_by_location()` (per-zone counts) |
| `orders.py` | `orders_for_customer()`, `order_count_for_customer()`, `order_count_by_location()` (per-zone counts) |
| `migration.py` | **The architectural piece.** `migrate_customer(cust_id, target_zone)` and `migrate_customer_to_country(cust_id, country_code)` for the two write paths. `migrate_batch_iter()` streams results from a thread pool. `iter_migrations_to_natural_regions()` and `iter_reset_to_us()` are the streaming entry points. `migration_status()`, `physical_shard_for_customer()` (via `explain.executionStats`), `shard_distribution_overview()` (via `$collStats`). `discover_zone_to_shard()` probes one customer per zone to map physical shard names to zone labels. Process-global job registry (`set_/get_/clear_active_job`) shared across Streamlit sessions for live-feed resiliency. |

### Scripts (`scripts/`, run via `python -m scripts.<name>`)
| Script | What it does |
|---|---|
| `check_env.py` | 5-stage check: env vars, MongoDB ping, sharded-cluster topology (mongos visible + `listShards`==3), zone regions detected in shard hostnames (warns rather than fails — Atlas hostnames don't always tag regions), `residency_demo` reachability |
| `create_db_index.py` | **Read-only verification** (despite the name — Atlas restricts sharding admin commands to Project Owners via UI). Confirms both collections are sharded on the expected keys (`{location, _id}` and `{location, customer_id}`) and prints per-shard chunk distribution from `config.chunks` |
| `verify_data.py` | 8 checks: customer count (100), order count (5000–10000), all country codes recognised, all customers initially in US zone via location="US", every order's location matches its parent customer's location, no orphan orders, country diversity per natural region (≥5 distinct per zone), sharded distribution visible on 3 shards via `$collStats` |
| `smoke_test.py` | 8 steps that prove the migration mechanism works end-to-end: pick an APAC-domiciled customer in US zone, migrate to APAC, verify logical state (zone) + physical state (shard via `explain`), migrate back, bulk-migrate to natural regions, verify each customer's `location == country`, reset all to US |

### Data pipeline (`data/`)
- `generate.py` — clears `residency_demo`, generates **100 customers** across ~35 cities in 29 countries (weighted by population/business density), each with **50–100 orders** (~7,500 total). All initial docs have `location: "US"` regardless of the customer's natural country — that's the demo's starting state. Deterministic (`random.seed(42)`). Takes ~30 seconds.

### UI (`app.py`)
Full-page Streamlit:
- **Sidebar**
  - **Demo controls** — 🌍 Migrate to natural regions / ↺ Reset all to US. Buttons disabled while a migration is in flight.
  - **Live cluster** (`@st.fragment(run_every=3)`) — per-zone customer + order counters labelled with **city only** (Virginia / Frankfurt / Singapore), zone-coloured border. The cloud provider and region are deliberately hidden here so the multi-cloud nature of the cluster stays a surprise during the demo. Updates during migration.
  - **Inspect** — customer dropdown; shows the customer's logical location (ISO code + zone), business address, natural zone, order count, and **physical shard** placement for customer doc + orders (via `explain.executionStats`). Shard labels resolve to friendly zone names via `discover_zone_to_shard()`.
  - **Cluster overview** (expander) — per-shard customer/order counts with zone-and-city labels (e.g. `atlas-xu2oze-shard-1 (EU zone · Frankfurt)`), plus the raw `listShards` output. Cloud provider intentionally omitted — same surprise-preservation as the Live cluster panel.
- **Main pane**
  - **World map** (`@st.fragment(run_every=3)`) — Plotly `scatter_map`, 100 customer business dots coloured by current data zone, 3 big data-centre markers in Virginia / Frankfurt / Singapore sized by tenant count, faint connection lines from each customer to their current data centre. Auto-refreshes during migration so dots transition zones in roughly real time. **Hovering a data-centre marker reveals the cloud provider and region** (e.g. `Frankfurt · Azure germanywestcentral`) — this is the one place in the UI where the multi-cloud nature is exposed, intended as a manual reveal beat during the demo.
  - **Live migration feed** (`@st.fragment(run_every=1)`) — appears whenever a migration is in flight or pending dismissal. Progress bar (X of N customers), scrolling completion rows with source code → target code, order count, elapsed seconds. Final summary + **Dismiss** button when complete.
  - **Customer roster** (`st.data_editor`) — sortable table with ID, Customer, Industry, Country, City, **Natural zone** (read-only), **Loc code** (read-only ISO country code stored on disk), **Data zone** (editable dropdown US/EU/APAC). Edit the Data zone column to queue migrations, click **Submit migrations**, the live feed appears as those targeted customers are processed.

### Documentation
- Updated root `README.md`: extended the status table to 7 PoCs, added prerequisites for PoC 7 (M30 Global Cluster cost warning), added the 7-step "Deploy the Data Residency PoC" walkthrough with explicit Atlas UI steps for Global Writes zone-code mapping, added the 7-beat demo flow, added project-layout entry.
- The source plan lives at `~/.claude/plans/read-the-files-in-valiant-lobster.md`.

## Problems we hit and fixed

This was the most fraught PoC of the set — Atlas's Global Cluster surface is more restrictive than the docs suggest, and several layers of assumptions had to be unwound.

| Symptom | Root cause | Fix |
|---|---|---|
| `create_db_index.py` script crashes immediately on `enableSharding` with "not authorized on admin" | Atlas restricts sharding admin commands (`enableSharding`, `shardCollection`, `splitChunk`, `moveChunk`) to Project Owners operating via the Atlas UI or Atlas Admin API. **No database-user role grants these**, including `atlasAdmin` and Atlas custom roles (which only expose `ENABLE_SHARDING`, `ADD_SHARD_TO_ZONE`, `UPDATE_ZONE_KEY_RANGE` — `shardCollection` is not in the subset) | Rewrote `create_db_index.py` as **read-only verification**. User shards the collections via Atlas UI: Data Explorer → collection → Global Writes tab → Shard Collection with the second-key field. Documented this prominently in the README. Pre-splitting per customer was also dropped (`splitChunk` is similarly locked). |
| After UI sharding, EU and APAC migrations both routed to the same physical shard (shard-1); shard-2 stayed empty | Atlas Global Writes validates location codes against ISO 3166-1 alpha-2 country codes. "EU" and "APAC" are **not** valid ISO codes (they're region names) so Atlas silently routed them to a fallback bucket. Two zones collapsing onto the same shard meant the demo's three-zone story was visually broken. | First attempt: use one representative ISO code per zone (US, DE, SG). User rejected — wanted each document to use the customer's actual ISO country code. Second attempt (final): each customer's `location` field = their real business country code. Added `ZONE_REPRESENTATIVE_CODE` (US/DE/SG) used **only** for cross-zone overrides when the customer's natural country isn't in the target zone. User maps all 29 ISO codes in Atlas Global Writes UI (one-time setup). |
| `migrate_customer` crashes with "Multi-update operations are not allowed when updating the shard key field" | MongoDB disallows `updateMany` on a shard-key field. | Switch to `bulk_write` with multiple `UpdateOne` ops. |
| `bulk_write` crashes with "Document shard key value updates that cause the doc to move shards must be sent with write batch of size 1" | Cross-shard doc moves must be sent as batch-size-1 writes. Neither `updateMany` **nor** `bulk_write` works for this. | Sequential `update_one` calls — one per order document. Each call is its own retryable write. |
| Sequential `update_one` in a multi-doc transaction took ~57 seconds for one customer (66 orders + customer doc) | Each cross-shard shard-key update is internally a two-phase commit (~1s). Wrapping 67 of them in an outer transaction added another ~25s of coordination and risked hitting Atlas's 60s transaction timeout. | Dropped the outer transaction — each per-doc shard-key update is already internally atomic. `migration_status.in_flight` flag detects mid-flight partial state; re-running the migration is idempotent. |
| Single-customer migration still took ~80s without transactions | Each cross-shard update is fundamentally ~1.5s due to MongoDB's internal two-phase commit. 66 sequential updates = 80+ seconds. Bulk migration of 100 customers would have taken hours. | Two-level parallelism: `ThreadPoolExecutor(max_workers=16)` for order updates within a customer, `ThreadPoolExecutor(max_workers=4)` for customers within a bulk batch. Single-customer migration: ~10s. Full 100-customer bulk: ~2 minutes. |
| The cluster overview inspector labelled APAC shard as "EU zone" after bulk migrate | `discover_zone_to_shard` queried `find_one({"location": "EU"})` etc., but after the ISO rewrite no customer had `location == "EU"` — they had real country codes like `DE`, `FR`. Discovery only found the US shard. | Rewrite `discover_zone_to_shard` to query `{"location": {"$in": codes_for_zone}}` — picks any customer whose location code maps to the zone. All three zones now resolve correctly. |
| Map didn't update during migration, nor after migration without a page refresh | Map was rendered inline in the main script body — only re-rendered on full script reruns, which don't happen during a background-thread migration. | Wrap the map in `@st.fragment(run_every=3)` so it auto-refreshes on its own timer with fresh customer data. |
| `verify_data.py` hung indefinitely on step 5 | The cross-sharded `$lookup` between `orders` and `customers` doesn't terminate quickly in this topology. | Rewrote step 5 to use a per-customer dict comparison instead of `$lookup`. Added `flush=True` to print statements so progress is visible on long-running checks. |
| Streamlit's blocking `st.spinner` froze the entire UI for ~2 minutes during bulk migration — worst beat in the demo | Synchronous calls to `migrate_all_to_natural_regions()` inside the action handler. | Refactored to streaming generators (`migrate_batch_iter`, `iter_migrations_to_natural_regions`, `iter_reset_to_us`) consumed by a background `threading.Thread`. New `@st.fragment(run_every=1)` panel renders a live scrolling feed of per-customer completions. Action buttons gated on `_migration_in_flight()`. |
| Browser refresh mid-migration lost the live feed (worker thread kept running but the new session couldn't see it) | The job dict lived in `st.session_state.migration_job` — per-session. A refresh created a new session with no knowledge of the running thread. | Moved the job to a **process-global registry** in `src/migration.py` (`_ACTIVE_JOB` + `threading.Lock` + `set_/get_/clear_active_job`). The worker thread writes to the same dict object that the registry holds. Any Streamlit session — fresh or refreshed — calls `get_active_job()` and finds the in-flight migration. |
| Couldn't directly verify the Streamlit UI from the headless tool environment | Streamlit catches Python errors and displays them in the UI rather than crashing the process. | Triangulated each fix by combining `py_compile`, `streamlit run --headless` + `curl localhost:8501` for HTTP 200, log-tail inspection for tracebacks, and direct CLI tests of the underlying functions (`./venv/bin/python -m scripts.smoke_test`). User confirmed visually after each UI change. |

## Smoke-test results (final)

All 8 steps pass against the live cluster:

| Step | Operation | Result |
|---|---|---|
| 1 | Pick an APAC customer in initial state | `cust-009 (Apex Solutions Inc, SG)` with 95 orders, all in US zone |
| 2 | Migrate to APAC | 11.5s, 1 customer + 95 orders updated |
| 3 | Verify logical zone state | customer + 95 orders all report `data_zone=APAC` |
| 4 | Verify physical shard placement (`explain.executionStats`) | customer + orders all on `atlas-xu2oze-shard-1` (the APAC zone shard) |
| 5 | Cluster-wide shard distribution (`$collStats`) | 99 customers + 7,447 orders on US shard; 1 customer + 95 orders on APAC shard; EU shard empty |
| 6 | Revert customer to US | 9s, customer + 95 orders restored to US zone |
| 7 | Bulk migrate to natural regions | 64 customers moved, 36 already in US zone. Result: US=36, EU=34, APAC=30 |
| 8 | Reset all to US | All 100 customers + 7,542 orders back in US zone |

End-to-end migration of a single 95-order customer: 11.5s with 16 parallel order workers. Bulk migration of 64 customers: ~2 minutes with 4 parallel customer workers.

## Deviations from the plan

The original plan held the architectural shape but was significantly rewritten after discovering Atlas's constraints:

- **No `create_db_index.py` sharding** — Atlas restricts admin commands; sharding is done via Atlas UI by a Project Owner. Script became read-only verification.
- **No pre-splitting per customer** — `splitChunk` is locked down. Demo works fine without it (coarser-chunk granularity is invisible to the audience).
- **Location codes are real ISO country codes**, not zone labels. "EU"/"APAC" silently fell into Atlas's fallback bucket. User maps 29 country codes in Atlas Global Writes UI (one-time).
- **No outer multi-doc transaction wrapping the per-customer migration** — Atlas's 60s transaction timeout vs. ~1s per cross-shard update made transactions impractical. Each per-doc shard-key update is internally atomic; partial state is detected by `migration_status.in_flight`.
- **Two-level parallelism** (16 order workers × 4 customer workers) added to compensate for the ~1.5s per-cross-shard-update floor. Not in the original plan; necessary for a tolerable bulk-migrate beat.
- **Live feed + non-blocking migration** added in a follow-up planning round. Worker thread writes to a process-global registry; `@st.fragment(run_every=1)` panel renders a scrolling event feed; map and sidebar refresh on their own 3-second cadence. Browser-refresh resiliency: the job lives at module scope in `src/migration.py`, not in `st.session_state`, so a refreshed tab picks up the in-flight job seamlessly.

## Final layout
```
data-residency/
├── app.py                  ← Streamlit (3 fragments: map / feed / sidebar live cluster)
├── requirements.txt
├── .env / .env.example
├── src/
│   ├── db.py
│   ├── zones.py            ← ZONE_FOR_COUNTRY (full ISO map), ZONE_REPRESENTATIVE_CODE
│   ├── customers.py        ← list_customers, customer_count_by_location
│   ├── orders.py           ← order_count_by_location, order_count_for_customer
│   └── migration.py        ← migrate_customer / migrate_customer_to_country,
│                             migrate_batch_iter (streaming), process-global job registry,
│                             physical_shard_for_customer, discover_zone_to_shard
├── scripts/
│   ├── check_env.py
│   ├── create_db_index.py  ← read-only verification (Atlas restricts admin commands)
│   ├── verify_data.py
│   └── smoke_test.py
└── data/
    └── generate.py         ← 100 customers + ~7,500 orders, all initial location="US"
```

## Run book (recap)

```bash
# Atlas UI setup (one-time):
#   1. Provision Global Cluster (M30+, three zones: US/EU/APAC mapped to
#      AWS us-east-1 / Azure germanywestcentral / GCP asia-southeast1)
#   2. Add user `pocuser` with readWriteAnyDatabase + clusterMonitor
#   3. Create empty customers + orders collections in residency_demo
#   4. Shard each collection via Data Explorer → Global Writes tab
#      (second shard-key field: _id for customers, customer_id for orders)
#   5. Map 29 ISO country codes to zones in Global Writes Custom Zone Mapping

cd data-residency && source venv/bin/activate
python -m scripts.check_env         # 5/5 OK (zone-region check WARN harmless)
python -m scripts.create_db_index   # verifies shard keys + chunk distribution
python -m data.generate             # 100 customers + ~7,500 orders, all in US
python -m scripts.verify_data       # 8 checks pass
python -m scripts.smoke_test        # 8 migration steps pass end-to-end
streamlit run app.py                # localhost:8501

# PAUSE THE CLUSTER WHEN DONE — $24/day while running.
```

## How a migration actually works (under the hood)

### The trigger — what code initiates it

The Python side issues an ordinary write. From `src/migration.py:migrate_customer_to_country`:

```python
# Customer document
db.customers.update_one(
    {"_id": cust_id, "location": source_code},
    {"$set": {"location": target_code}},
)

# Each order document — one update_one per order
for oid in order_ids:
    db.orders.update_one(
        {"_id": oid, "customer_id": cust_id, "location": source_code},
        {"$set": {"location": target_code}},
    )
```

No `moveChunk`, no `sh.X` admin commands, no balancer toggling. Application code does not know it's living in a sharded cluster. The "migration" — the physical relocation of bytes across continents — is a side effect of updating a field.

### What the mongos does on receiving the update

The mongos router holds the cluster-wide chunk map. For `customers` (shard key `{location: 1, _id: 1}`):

1. Looks up the **current** shard-key value (e.g. `{location: "US", _id: "cust-009"}`) — that chunk is owned by `atlas-xu2oze-shard-0` (US zone).
2. Looks up the **new** shard-key value (`{location: "SG", _id: "cust-009"}`) — that chunk is owned by `atlas-xu2oze-shard-2` (APAC zone, per the zone tag range Atlas configured when `SG` was mapped to APAC).
3. Notices the new shard-key value routes to a **different shard** than where the document currently lives. This is the cross-shard-update case.

If source and target shards were the same, mongos would do a plain in-place update. They aren't, so:

### The internal cross-shard transaction

For cross-shard updates of shard-key values, MongoDB runs a distributed two-phase commit transparently. The mongos:

1. **Starts an internal transaction.** A 2PC coordinator gets chosen from the participating shards.
2. **Inserts the document on the destination shard** with the new shard-key value — `atlas-xu2oze-shard-2` gets a fresh `cust-009` doc with `location: "SG"`.
3. **Deletes the document from the source shard** — `atlas-xu2oze-shard-0` removes its copy.
4. **Two-phase commit** — coordinator instructs both shards to either commit or abort atomically. If either side fails, both roll back.

From the application's perspective: a single `updateOne` call that takes ~1–1.5s and returns `modified_count: 1`. From the cluster's perspective: a coordinated multi-shard transaction. **The application never opened a transaction, never knew about it, never had to handle one.** That's the fool-proof part.

This is why each individual `update_one` took ~1.5s in our PoC — the floor is two-phase-commit latency across regions (US-East → Singapore for some of them), not MongoDB doing anything inefficient. With 16 parallel order workers per customer, we amortize that floor to ~10s for a 75-order customer.

### What makes the document end up on the right physical region

The `location` shard-key prefix does the routing work. Atlas's Global Writes configuration defines **zone tag ranges**:

- `{location: "US", _id: MinKey}` → `{location: "US", _id: MaxKey}` is **tagged for zone US**
- Same for `DE`, `FR`, `GB`, … → EU zone
- Same for `SG`, `JP`, `AU`, … → APAC zone

Zones are bound to physical replica sets when the Global Cluster is provisioned. In this PoC the binding is **multi-cloud**: US zone → AWS `us-east-1` replica set, EU zone → Azure `germanywestcentral`, APAC zone → GCP `asia-southeast1`. The balancer enforces that **any chunk whose key range falls inside a zone tag must live on that zone's shard**, regardless of cloud provider. If a chunk drifts out of zone (e.g. because a document was inserted with a brand-new country code), the balancer migrates it to the right shard within minutes — and the cross-cloud aspect means that migration can be an AWS↔Azure or GCP↔Azure data move under the hood, with the application none the wiser.

So when you `$set: {location: "SG"}` on a document, the new key value lands inside the APAC zone's tag range, and Atlas's enforcement guarantees that chunk lives on the Singapore shard. **The country code in the document IS the residency policy.**

### What the audience sees

In the PoC's UI:

- **Live cluster sidebar** — per-zone customer/order counters update every 3s via `customer_count_by_location()` / `order_count_by_location()` (simple `$group` aggregations).
- **World map** — every 3s, each customer's dot recolors as their `location` code lands in a different zone. Lines flip from US/Virginia to Frankfurt/Singapore. The data-centre marker hover tooltip is the only place that exposes the cloud provider — useful as a "wait, that's Azure?" reveal beat mid-demo.
- **Migration feed** — every 1s, scrolling rows like `✓ cust-009  US→SG (US → APAC)  95 orders  6.1s`. The feed is a fragment polling a process-global job dict that a worker thread is appending to.
- **Shard inspector** — runs `db.runCommand({explain: {find: "customers", filter: {_id: "cust-009"}}, verbosity: "executionStats"})` and surfaces which shard actually returned the document. That's the receipt that proves it physically moved — not a label change, an actual document on a Singapore-region replica set.

## What MongoDB does to make this easy

The platform wins the demo lands on:

1. **The application is unchanged.** The same `update_one(...)` works whether you're on a single-region M10 or a three-region Global Cluster. No driver flags, no per-region connection strings, no DNS routing layer, no client-side router.
2. **One connection string.** The mongos handles every routing decision. Reads to a customer's data implicitly target the right region; the driver's read preference can also pin to local-zone reads via `readPreference: nearest` + tag sets (one line of code, not implemented in this PoC).
3. **Cross-shard atomicity is automatic** when you update the shard-key value. You don't open a `session`, you don't call `commitTransaction`, you don't write rollback handlers. The mongos coordinates the 2PC for you. Failure during the move leaves the doc on the source shard untouched.
4. **The shard key is mutable** (MongoDB 5.0+). Most "shard key" implementations historically made the routing key effectively immutable; changing residency required dump-and-reload. Updating the shard-key value in-place is the feature that makes "change residency with one field write" possible.
5. **Zone-tag enforcement is declarative.** You map country codes → zones once in the Atlas UI; the balancer maintains that invariant forever. New documents land in the right region from the first insert. Drift is auto-corrected.
6. **Online migration.** Reads and writes continue against the moving documents throughout the operation. The mongos refreshes its chunk map and re-routes in-flight queries. No maintenance window.
7. **The same query language everywhere.** Aggregations, secondary indexes, transactions, change streams all work across the sharded collection without any application-side fan-out logic.

That bundle is the platform win, not any single feature in isolation.

## Competitor comparison

### Genuinely competitive on data residency

- **Google Cloud Spanner** — Spanner's "leader placement" + multi-region instance configs is arguably the gold standard. Geo-partitioning is declared in DDL; rows route by primary-key prefix; cross-region writes use Paxos, not 2PC.
  - **Better than MongoDB:** strict serializable global transactions, no shard-key-update gymnastics, primary-key changes via interleaved tables.
  - **Worse:** SQL/relational only, no document model, very expensive at small scale, no "change a row's region with a field update" idiom — you usually re-insert into a different partitioned table.
  - **Verdict:** "better" if you live in SQL-land and your residency model maps cleanly to row-level tenancy; "worse" if you have heterogeneous schemas or want residency policy to be a mutable field.

- **CockroachDB** — The most direct competitor. CockroachDB has explicit `REGIONAL BY ROW` tables where each row has a `crdb_region` column that controls placement. `UPDATE customers SET crdb_region = 'eu-central-1' WHERE id = ...` does exactly what MongoDB's shard-key update does, with similar 2PC semantics.
  - **Better:** the syntax is more honest (region is a first-class concept, not a string in a shard-key prefix), and the query optimiser is more aware of region cost.
  - **Comparable:** underlying mechanism is the same flavour of cross-region 2PC, with the same latency floor.
  - **Worse:** Cockroach's per-row geo-partitioning at scale is operationally heavy; MongoDB's zone sharding is more forgiving when zones contain many countries with mixed traffic shapes. No document model.

- **YugabyteDB** — Similar to CockroachDB (Postgres-compatible, geo-partitioned). Tablespaces + leader-preference policies do the work. Mechanically very similar to MongoDB's zone sharding but more declarative on the DDL side.

### Worse or non-comparable

- **DynamoDB Global Tables** — DynamoDB's "global tables" are a different model: **active-active multi-region replication of the entire table**, not partitioned-by-region single-source storage. Every region has a full copy. For pure data-residency compliance, **this is actively wrong** — your EU data is also in the US. You can build a residency story by sharding application-side across separate per-region DynamoDB tables, but then you've reinvented the routing layer that the MongoDB pitch eliminates. Glaring gap.

- **Cosmos DB** — Multi-region writes are well-supported, but row-level residency requires custom partition-key design plus explicit region pinning at the API level. Doable, but it's an application concern, not a database concern. Closer to "DynamoDB + write regions" than to MongoDB's zone sharding.

- **Cassandra / ScyllaDB** — Network topology strategy lets you place replicas per DC, but moving a single row between DCs typically means dual-writing and cleaning up — not a single field update. The "residency follows a field" idiom isn't native.

- **Postgres + Citus** — Citus can do tenant-isolated sharding, but cross-shard ACID is limited and there's no built-in zone-tag enforcement. You can build a residency story but you'll write a lot of the routing yourself.

- **Aurora / RDS / single-region SQL** — Not in the running. Residency means one cluster per region plus an application-side router. Exactly the architecture the PoC is arguing against.

### Bottom line

If the buyer's question is "I want one platform that lets a field on a document determine which country's datacentre hosts it, with online migration via a field update, and an unchanged application," the realistic alternatives are:

1. MongoDB Atlas Global Clusters (this PoC)
2. CockroachDB `REGIONAL BY ROW`
3. YugabyteDB geo-partitioned tablespaces
4. Spanner with region-aware schema design (more rigid, more powerful, more expensive)

DynamoDB, Cosmos, Cassandra, and traditional SQL solutions force you to build the routing layer yourself. That's the part of the architecture MongoDB's pitch is asking you to delete.

MongoDB-specific edges over Cockroach/Yugabyte: the document model and the operational simplicity of Atlas-managed shard provisioning. Edges over Spanner: cost at small scale and the document model. Edge over everyone: the demo audience can watch a row physically move between continents in a few seconds by editing a single cell in a table — and that demo lands because every layer underneath is consistent with the story.

## Outstanding

- **Cost is the elephant.** M30 Global Cluster across 3 regions is ~USD 24/day. Must be paused between sessions. The pause/resume cycle takes ~5 min on resume. If running daily for a multi-week pilot, this is ~USD 720/month — 12× the M10 cost of the other PoCs.
- **Atlas zone-code mapping is manual setup.** 29 ISO codes mapped to zones, one-time but tedious in the Atlas UI. Could be automated via Atlas Admin API if the demo is run repeatedly; not worth it for a one-shot.
- **Per-customer chunk inspection isn't exposed.** `physical_shard_for_customer` uses `explain.executionStats` which only shows shards that returned results; we don't surface the underlying chunk ID or chunk boundaries. Not load-bearing for the demo, but a more technical audience might ask.
- **The 7th plan file at `mongodb_poc/plans/`.** Following the precedent of PoC #6, the source plan lives in `~/.claude/plans/` and the README walkthrough is canonical. Promote to `plans/07_*.md` if presented externally.
- **Sharded `$lookup` is broken in this topology** (hung indefinitely during dev). If anyone wants to do cross-shard joins for the demo, they'll need to redesign — likely with per-customer queries plus client-side join.
- **No automated cleanup script.** Atlas Global Clusters can't be torn down via API in this PoC's setup; user manually pauses/terminates via the Atlas UI.
