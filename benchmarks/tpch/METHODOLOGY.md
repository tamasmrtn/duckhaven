# TPC-H benchmark methodology: DuckHaven vs Snowflake vs Databricks

> **Status: frozen, revised twice.** First registered in
> `db/results.duckdb`'s `methodology_registrations` table on the first
> real Phase 0 run, 2026-08-07. Revision 1 (same day) narrowed scope to
> read scenarios and resized compute for a fair trial-account comparison —
> see §9. **Revision 2 (same day) drops Azure entirely**: DuckHaven runs
> locally for the whole benchmark, not just Phase 0, and the comparison is
> now SF1 (done) / SF10 / SF100 / SF300 — SF1000 is out of scope, since it
> was only ever feasible on paid Azure infrastructure this project no
> longer uses. `methodology_registrations` is the authoritative,
> timestamped record of every version's hash; this document doesn't quote
> its own (a hash can't quote itself). The SF1 read comparison under
> comparable trial-tier compute is complete and clean: 726/726 work items
> done, 0 errors, across all three engines.

## 1. Purpose and scope

DuckHaven's own documentation makes a cost/performance claim against
Snowflake and Databricks. Until this benchmark, that claim was backed only
by third-party citations (a 2023 SF100 blog post; DuckDB Labs' own
SF3000/SF10000 laptop post) — numbers from a different engine (raw DuckDB,
not DuckHaven), a different environment, and a methodology this project
had no part in designing or verifying.

This benchmark replaces those citations with first-party numbers: the
standard TPC-H workload, run through all three engines' own real client
paths, on infrastructure this project provisioned, measured, and can
reproduce. It is not a marketing exercise: the same fixed, small compute
size runs at every scale factor (§7), specifically so growing data volume
shows where each engine — DuckHaven included — stops being comfortable,
not just where DuckDB does.

Both Snowflake's and Databricks' current Terms of Service permit
publishing benchmark results (both have dropped the traditional "DeWitt
clause") on the condition that enough detail is published to replicate the
test. This document, together with the published raw results in
`db/results.duckdb` and the redacted public export `report/` produces, is
that detail.

## 2. Engines under test

| Engine | Client | Compute | Notes |
|---|---|---|---|
| DuckHaven | `duckhaven-sql-connector` (PyPI), via the SQL Sessions API | A local Docker elastic agent (`deploy/docker-compose.elastic.yml`), pinned to one fixed-size agent via `DUCKHAVEN_AGENT_ID` (§7) | The engine actually under test; everything else is a comparator. Runs entirely on the machine running this benchmark — no cloud deployment. |
| Snowflake | `snowflake-connector-python` (official SDK) | `SNOWFLAKE_LEARNING_WH`, a fixed X-Small this trial account cannot resize (§7) | Trial ("Snowflake Learning") account, no separate warehouse-creation privilege — see §9's errata. |
| Databricks | `databricks-sql-connector` (official SDK) | A Serverless SQL Warehouse, set to 2X-Small (§7) | Confirmed AWS-hosted trial workspace (via `GET /api/2.0/clusters/list-node-types`, all-AWS node-type naming). Loads from the same local corpus DuckHaven does (§6) — no cross-cloud replication needed since both load straight from local disk. |

All three are driven through **persistent, session-scoped connections**,
not one-shot/stateless query APIs and not raw engine internals (no direct
DuckDB access for DuckHaven) — the same kind of client a real analytics
team would use, so each engine's own client-roundtrip overhead is part of
what gets measured, not something engineered away for DuckHaven alone.

Every client implements the same `EngineClient` interface
(`src/tpch_bench/clients/base.py`: `connect()` / `run_statement()` /
`close()`), so the orchestrator drives all three identically — the same
shape of client, the same scenario loop, per engine.

## 3. Query corpus

The 22 standard TPC-H queries, at `queries/tpch_canonical/`, taken from
DuckDB's own `tpch` extension (`tpch_queries()`), including its CTE
rendering of Q15 in place of the spec's `CREATE VIEW`/`DROP VIEW` pair —
CTEs behave identically across all three engines; views' transaction and
temp-vs-permanent semantics don't.

`queries/dialect/render_dialects.py` deterministically copies the
canonical text into `queries/dialect/<engine>/` for each engine, applying
zero or more declared, logged overrides (`queries/dialect/DIFFS.md`).
**No overrides are declared as of this writing** — all 22 queries run
unmodified on every engine. Overrides get added only once Phase 0's dry
run surfaces a real incompatibility running the canonical text against a
live engine, never speculatively ahead of that. `DIFFS.md` is generated,
not hand-edited, so "what changed and why" can never drift from what
actually runs.

## 4. Scenarios

**In scope as of the 2026-08-07 revision (§9): read scenarios only.**
Three scenarios (`config/scenarios.yaml`), reps and statistic
pre-declared so results can't be cherry-picked after the fact:

| Scenario | What it measures | Reps |
|---|---|---|
| `sequential` | All 22 queries, one at a time, on a warm connection. | 5 |
| `cold_start` | Same 22 queries, connection closed and reopened before each one — isolates the "resume from zero" cost each platform charges (an elastic DuckHaven agent scaled to zero, a suspended Snowflake warehouse, a stopped Databricks Serverless Warehouse). | 3 |
| `concurrent` | All 22 queries fired at once, one connection per worker thread (no client SDK here is safe to share a connection across threads). | 3 |

**Statistic: median** across reps, pre-declared — no post-hoc selection of
"best run."

DuckHaven-specific handling for these three: no `SET duckhaven_concurrency`
override is issued at all, so the agent runs under its own default
`auto` admission profile (`shared/src/duckhaven_shared/concurrency.py`'s
`DEFAULT_PROFILE`) — the "pure defaults, no manual tuning" impartiality
rule in §8 applies to compute configuration too, and `auto` *is* that
default, not a special mode chosen for this benchmark.

### 4.1 Implemented but out of scope: `write` and `dml`

The harness still implements two write scenarios
(`orchestrator/scenario_write.py`, `scenario_dml.py`, `ddl/`) — a
CTAS-equivalent materialization in **narrow** and **wide** table shapes,
and a DELETE+INSERT incremental-refresh cycle against them. They are not
part of the active comparison: Phase 0's SF1 dry run showed they aren't
fairly comparable across all three engines today, for engine- and
account-specific reasons with nothing to do with query correctness (§9's
errata has the detail). Before every `write`/`dml` statement, the session
issues `SET duckhaven_concurrency = 'single'` (not itself a measured work
item) — that pre-statement's actual effect is also corrected in §9. Kept
in the codebase rather than deleted, in case a future account/setup makes
them comparable again; not drawn from by `config/scenarios.yaml`'s
`all_scenarios` or any scale factor's active scope.

DuckHaven's Iceberg maintenance is **read-only/advisory only** — it
cannot expire snapshots or rewrite files via the DuckDB iceberg extension,
so `dml`'s repeated DELETE+INSERT accumulates small files/snapshots with
**no in-app remediation**. Noted here for whenever `dml` re-enters scope,
not something that affects the read-only comparison today.

## 5. Scale factors

| SF | Approx. size | Scenarios run | Query timeout | Engines |
|---|---|---|---|---|
| SF1 | ~1 GB | `sequential`/`cold_start`/`concurrent` | 600s | All three — complete, see status above |
| SF10 | ~10 GB | `sequential`/`cold_start`/`concurrent` | 600s | All three |
| SF100 | ~100 GB | `sequential`/`cold_start`/`concurrent` | 1800s | All three |
| SF300 | ~300 GB | `sequential`/`cold_start`/`concurrent` | 3600s | DuckHaven + Databricks only — see §6 |

**SF1000 is out of scope**, not scoped down: it was only ever feasible on
paid Azure infrastructure large enough to hold a ~1 TB Iceberg copy of the
corpus, and revision 2 (§9) retired that plan entirely — this project runs
DuckHaven locally now, on the same machine as everything else, with no
cloud deployment to size up for it.

Every scale factor runs on the same fixed compute (§7) as SF1 — nothing
scales up with data size, because none of the three trial
accounts/setups here *can* be resized. That is a deliberate reading of a
real constraint, not a limitation this project is hiding: it means SF100
and SF300 show how each engine's smallest practical tier handles 100x and
300x the data, which is a fair question on its own even though it isn't
the "$/hr-matched bigger warehouse" comparison earlier drafts of this
document described.

## 6. Data generation and placement

`tpchgen-cli` (PyPI, a compiled Rust TPC-H generator) generates each scale
factor's Parquet corpus locally (`src/tpch_bench/datagen/tpchgen_runner.py`),
chosen over the reference `dbgen` for the throughput SF100/SF300 need.

**DuckHaven and Databricks load from this same local corpus** —
byte-identical source data is the fairness requirement §8's impartiality
rules exist to protect. Since revision 2 (§9) retired the Azure
deployment, both engines now load directly from local disk rather than
through any cloud replication step:

- DuckHaven: `src/tpch_bench/load/duckhaven.py` uploads each file to the
  session's presigned staging URL (`Connection.stage_files`) and issues
  `CREATE TABLE ... AS SELECT * FROM read_parquet(get_url)` — the same
  path `dlt-duckhaven` uses, landing in the local Docker deployment's
  bundled MinIO object storage.
- Databricks: `src/tpch_bench/load/databricks.py` uploads each file to a
  Unity Catalog Volume via the Files API and issues `CREATE TABLE ... AS
  SELECT * FROM read_files(...)` — Databricks has no session-scoped
  staging equivalent, so a Volume is the closest analogue.

`src/tpch_bench/datagen/corpus.py`'s Azure Blob/S3 manifest-and-replicate
functions, built for the now-retired Azure plan, are unused by this flow.
Kept in the codebase rather than deleted (the same "implemented, not
drawn from" treatment as `write`/`dml` in §4.1), in case cross-machine
corpus sharing becomes useful again.

**Snowflake is the deliberate exception**: it reads its own free,
pre-loaded `SNOWFLAKE_SAMPLE_DATA.TPCH_SFxxx` schemas for SF1/SF10/SF100 —
zero load cost, and the same data a real Snowflake evaluation would reach
for first. **SF300 has no pre-loaded Snowflake sample, and this project's
read-only scope (§4.1) leaves no way to self-load one** — Snowflake is
excluded from the SF300 comparison entirely rather than worked around.
This is a **disclosed, intentional gap**, not normalized away: normalizing
it away would mean either loading Snowflake's smaller scale factors
unnecessarily (real, avoidable cost against a bounded trial credit) or
fabricating an "unloaded" cost for what most real Snowflake users never
pay.

## 7. Sizing

**One fixed compute size, used at every scale factor (SF1 through
SF300) — not a $/hr-matched tier that scales up with data, because none
of the three trial accounts/setups here can be resized:**

| Engine | Size | Why this and not something bigger |
|---|---|---|
| Snowflake | `SNOWFLAKE_LEARNING_WH`, fixed X-Small | This "Snowflake Learning" trial account has no `MODIFY` grant on the warehouse — confirmed via a live `003001` insufficient-privileges error (§9 errata). It cannot be resized, full stop. |
| Databricks | Serverless Starter Warehouse, set to 2X-Small | Its own floor — the closest either platform's tier names get to Snowflake's fixed X-Small. |
| DuckHaven | A `DUCKHAVEN_AGENT_ID`-pinned elastic agent, 2 vCPU / 4 GB | The platform's own stated default size (`ELASTIC_DEFAULT_CPU`/`ELASTIC_DEFAULT_MEMORY_GB`), chosen to sit in the same class as the other two rather than because it's a hard floor — DuckHaven's Docker backend *could* run a bigger agent, but sizing it up while the other two stay pinned to their floor would stop being a comparison of comparable compute. |

"Comparable" here means each platform's practical floor under these
specific trial accounts and this specific choice for DuckHaven — not a
verified equal-$/hr match the way a paid-tier comparison would use
(`GET /admin/agents/compute-options`'s live pricing exists and still gets
used for cost accounting, just not for choosing a bigger size at SF100/
SF300). See `config/sizing_matrix.yaml` for the full reasoning and the
single `phase0_trial` tier definition (the name is a holdover from when
it was Phase-0-only; it now applies to every scale factor in scope).

Snowflake and Databricks warehouses auto-suspend on their own defaults;
`AUTO_SUSPEND`/`AUTO_RESUME` could not be explicitly set on the fixed
Snowflake warehouse for the same privilege reason as its size.

## 8. Impartiality rules

Locked in before any real-money run:

1. **Standard, unmodified TPC-H queries.** Any dialect translation is
   minimal, generated deterministically, logged in `DIFFS.md`, and never
   hand-edited (§3).
2. **No manual tuning** — no indexes, no clustering keys, no query hints,
   on any platform. Pure defaults, matching what a real small team gets
   out of the box, and matching DuckHaven's own "transparent, unoptimized
   compute" positioning.
3. **Cost from each platform's own authoritative billing**, never
   estimated: Snowflake `ACCOUNT_USAGE`, Databricks `system.billing.usage`,
   DuckHaven's `hourly_cost × elapsed` (computed from its own
   `infra_events`, no billing-latency wait needed on that side unlike the
   other two).
4. **Full raw results published**, not just headline numbers — both the
   fairness argument for this benchmark and the ToS condition both vendors
   attach to permitting disclosure at all.

### 8.1 Known, disclosed asymmetries

These are real differences in what each engine's *own real client SDK*
requires to answer the same question — not bugs, and not normalized away,
because doing so would mean measuring something other than each engine's
actual client-roundtrip cost:

- **Row counts.** DuckHaven's and Snowflake's server responses report a
  query's row count without the client fetching any rows. Databricks'
  connector does not — `databricks-sql-connector` only knows a `SELECT`'s
  row count after `fetchall()`. This client fetches all rows for
  Databricks to answer the same question the other two get for free,
  because that fetch is itself part of a real client's cost on this
  engine, not a shortcut to avoid.
- **Per-query timing/scan detail.** DuckHaven (`GET /queries/{id}/profile`)
  and Snowflake (`INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION`, near-real-
  time) both expose duration/bytes-scanned/spill detail synchronously,
  right after a query finishes. Databricks' only authoritative source for
  this, `system.query.history`, lags live activity — typically minutes,
  sometimes longer — so querying it inside the timed call would usually
  return nothing yet, and would fold an unrelated system-table scan into
  the very timing being measured. Databricks' recorded results therefore
  carry `client_wall_ms` and `row_count` from the run itself, with
  `server_duration_ms`/`bytes_scanned`/etc. backfilled by a later
  reconciliation pass against `system.query.history`, joined by
  `engine_query_id` — not fabricated at run time.
- **Databricks OAuth token lifetime.** The `databricks-sql-connector` has
  no built-in auth path for a plain (non-Azure) Databricks service
  principal, so this client mints its own OAuth client-credentials token.
  A minted token is valid for about an hour and is not refreshed under an
  open connection — a `sequential` scenario approaching that lifetime at a
  large scale factor is a real risk, not yet observed in practice. Noted
  here rather than engineered around speculatively.

## 9. Freezing and errata

Freezing happens by hashing this file's contents and calling
`Ledger.register_methodology(methodology_hash, doc_path)`
(`methodology_registrations`, append-only, keyed by hash) — done once, as
part of Phase 0's dry run, not before. Every `work_item` recorded from
that point on carries the frozen hash it was run under.

After freezing: **no edits to query text, scenario definitions, or rep
counts.** A genuine bug found later (a mislabeled column, a scenario
description that doesn't match what the code actually does) gets a dated,
additive erratum appended below, never a silent rewrite of the section
above it.

**Revisions are different from errata, and narrower.** An erratum
corrects a factual mistake in the frozen text — what it already claimed
was wrong. A revision changes what's being measured (scope, sizing) and
is legitimate only while still inside Phase 0, before any real-money
comparison run: that's what a dry run is *for*, distinct from adjusting
methodology after seeing results on the actual paid comparison, which
this project treats as the line real cherry-picking crosses. Each
revision below is dated, records what changed and why, and re-freezes
(a new hash, registered alongside the original — see
`methodology_registrations`, never replacing it).

### Revisions

**2026-08-07 — Scope narrowed to read scenarios; compute sized to match
across trial accounts.** Directed after reviewing Phase 0's SF1 dry run
results (§9 errata below): `write`/`dml` are no longer part of the active
comparison (§4.1) — kept implemented, not deleted, in case a future
account/setup makes them comparable. DuckHaven's read scenarios now run
under the agent's default `auto` concurrency profile (no override
issued), rather than the `single` override that only ever applied to the
now-out-of-scope writes. All three engines' compute was resized to each
platform's practical floor under these specific trial accounts —
`phase0_trial` in §7 — so the SF1 read comparison is apples-to-apples
rather than whatever each account happened to default to. Every SF1 read
result from before this revision was produced under mismatched sizing
(DuckHaven's pool-triggered default agent, Databricks' original Small
warehouse) and has been superseded by a re-run under `phase0_trial`
sizing, not kept alongside it.

**2026-08-07 — Azure retired; scope now SF1/SF10/SF100/SF300, no
SF1000.** Directed the same day as the first revision. DuckHaven runs
locally (Docker) for the entire benchmark now, not only Phase 0 — the
planned Azure deployment, the €30 budget it was scoped against, and the
"Phase 0 dry run / Phase 1+ paid run" split in earlier drafts of this
document are all retired along with it. SF1000 is dropped rather than
scoped down further (§5): it was only ever feasible on paid Azure
infrastructure sized for a ~1 TB corpus, which no longer exists in this
project's plan. `phase0_trial` sizing (§7) now applies at every scale
factor rather than only SF1, since none of the three trial
accounts/setups can be resized regardless of data volume. `write`/`dml`
stay out of scope, unaffected by this revision. Also fixed while making
this change, not itself a scope decision: the local DuckHaven agent's
`hourly_cost` was reporting Azure list-price rates
(`ELASTIC_AZURE_PRICE_VCPU_HOUR`/`_MEMORY_GB_HOUR`, unset, defaulting to
non-zero) despite running on already-owned hardware with `currency`
correctly reporting null — the two settings aren't linked the way that
might suggest. Zeroed per `docs/deployment/homelab-elastic-setup.md`'s
own guidance; every `cost_facts` row recorded before this fix used the
wrong (Azure-priced) figure for DuckHaven specifically and should be
treated as unreliable for cost comparison, though not for timing.

### Errata

**2026-08-07 — §4's `duckhaven_concurrency` claim was wrong.** Surfaced by
a real OOM during Phase 0's DuckHaven dry run (the `write` scenario's
`wide` shape, on the default-sized local elastic agent) and verified
against the agent source
(`agent/src/agent/control/channel.py::_build_request`,
`agent/src/agent/executor/admission.py`,
`shared/src/duckhaven_shared/concurrency.py`): `SET duckhaven_concurrency
= 'single'` does **not** give an unestimable write (any CTAS/INSERT/
DELETE — EXPLAIN-based sizing only covers a bare `SELECT`) the whole agent
memory budget. It changes how many queries share the admission budget
concurrently; the fallback bucket such a write is pinned to
(`estimate_fallback_bucket`, default `"M"` = ⅓ of the agent's *total*
budget) is fixed regardless of that setting. §4's original text and this
harness's `scenario_write.py` docstring both overstated what the
pre-statement does — both have been corrected. The actual levers for a
write that needs more memory are provisioning a larger agent for that run,
or raising `ESTIMATE_FALLBACK_BUCKET` on the agent; this harness does not
wire up either yet, so a `write`/`dml` OOM on the `wide` shape at larger
scale factors should be expected, not treated as a harness bug, until that
is built.

**2026-08-07 — Snowflake's `write`/`dml` scenarios did not run at SF1: a
real account limitation, not a harness bug.** The Snowflake trial account
used for Phase 0 is a "Snowflake Learning" account; its only role
(`SNOWFLAKE_LEARNING_ROLE`) has no `CREATE DATABASE` or `CREATE WAREHOUSE`
grant on the account, confirmed via a live `003001` insufficient-privileges
error attempting each. All 12 `write`/`dml` work items for Snowflake at
SF1 are recorded `failed` (6) or correctly left `pending` by the
DELETE-then-INSERT guard (6, since their paired deletes failed first —
see the `dml` scenario's own docstring). Read scenarios are unaffected:
`sequential`/`cold_start`/`concurrent` all completed 100% clean against
`SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`. A production-tier Snowflake account with
its own database-creation grant would not hit this; disclosed here
because this specific trial account did, and the honest result is more
useful than silently skipping those scenarios.

A related, real harness bug this surfaced and fixed: `build_client`
(`cli.py`) was passing the same scale-factor config to `write`/`dml` as to
the read scenarios, so *before* hitting the account's privilege limit, a
write at SF1 first failed by landing against the read-only shared
`SNOWFLAKE_SAMPLE_DATA` database. Fixed so `write`/`dml` never receive the
sample-schema config, on any engine or scale factor — they need a
writable target by definition.

## 10. Budget and guardrails

No cloud infrastructure spend: DuckHaven is local Docker compute on
already-owned hardware (§7, §9's second revision), with no per-hour rate
of its own. The only real resource at risk is each trial's bounded
credit pool:

| Item | Pool | Notes |
|---|---|---|
| Snowflake | $400 trial credit, 30 days | Fixed X-Small warehouse (§7) bounds the *rate* of consumption; SF100/SF300 still mean much longer-running queries than SF1 did, so total consumption isn't flat across scale factors even at fixed size. |
| Databricks | $400 DBU trial credit, 14 days | Same shape: Serverless 2X-Small is fixed, but SF100/SF300 query duration is not. |

Local disk is a second real constraint SF300 runs into directly: the
generated corpus (~300 GB) and DuckHaven's own local MinIO copy would
together exceed a typical workstation's free space if both are kept
simultaneously. The SF300 corpus task deletes each table's local Parquet
file as soon as both DuckHaven and Databricks have loaded it, rather than
holding the whole corpus until the end.

Guardrails that still apply:

- Snowflake/Databricks warehouses auto-suspend on their platform defaults
  (§7); nothing here is billed on an always-on basis.
- Before SF100 and SF300 specifically, check remaining trial credit and
  days-remaining on both accounts — an explicit go/no-go, not an assumed
  green light, the same discipline the retired Azure plan applied to its
  own SF1000 checkpoint.

## 11. Reproducibility

What gets published: this document, the full raw `db/results.duckdb`
(work items, per-query results, cost facts, infra events — everything
this benchmark recorded), and a redacted export with any credential-
adjacent fields stripped. Anyone with the same trial-tier access to all
three platforms — or paid access, since none of this depends on trial-only
behavior except the credit pools funding it — can rerun `queries/dialect/`
against the same generated corpus and compare.
