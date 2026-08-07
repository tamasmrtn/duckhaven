# TPC-H benchmark methodology: DuckHaven vs Snowflake vs Databricks

> **Status: drafted, not yet frozen.** This document becomes binding the
> moment its sha256 hash is registered in `db/results.duckdb`'s
> `methodology_registrations` table (`Ledger.register_methodology`), which
> happens as part of Phase 0's dry run — not before, so nothing here is
> locked in until it has actually been exercised against all three engines
> at SF1 on free/local tiers. Once frozen, query text, scenario
> definitions, and rep counts do not change; only dated, additive errata
> (§9) are allowed.

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
reproduce. It is not a marketing exercise — SF1000 is included
specifically to show where DuckHaven/DuckDB stops being competitive, not
to omit it.

Both Snowflake's and Databricks' current Terms of Service permit
publishing benchmark results (both have dropped the traditional "DeWitt
clause") on the condition that enough detail is published to replicate the
test. This document, together with the published raw results in
`db/results.duckdb` and the redacted public export `report/` produces, is
that detail.

## 2. Engines under test

| Engine | Client | Compute | Notes |
|---|---|---|---|
| DuckHaven | `duckhaven-sql-connector` (PyPI), via the SQL Sessions API | Azure Container Instances elastic agent (`deploy/terraform/examples/quickstart`) | The engine actually under test; everything else is a comparator. |
| Snowflake | `snowflake-connector-python` (official SDK) | A dedicated virtual warehouse per sizing tier (§5), `AUTO_SUSPEND = 60`, `AUTO_RESUME = TRUE` | Trial account, $400/30-day credit, separate from the Azure budget. |
| Databricks | `databricks-sql-connector` (official SDK) | A Serverless SQL Warehouse only (§8.2) | Confirmed AWS-hosted trial workspace (via `GET /api/2.0/clusters/list-node-types`, all-AWS node-type naming) — the corpus is replicated to S3 (§6) so it reads same-cloud. |

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

Five scenarios (`config/scenarios.yaml`), reps and statistic pre-declared
so results can't be cherry-picked after the fact:

| Scenario | What it measures | Reps |
|---|---|---|
| `sequential` | All 22 queries, one at a time, on a warm connection. | 5 |
| `cold_start` | Same 22 queries, connection closed and reopened before each one — isolates the "resume from zero" cost each platform charges (an elastic DuckHaven agent scaled to zero, a suspended Snowflake warehouse, a stopped Databricks Serverless Warehouse). | 3 |
| `concurrent` | All 22 queries fired at once, one connection per worker thread (no client SDK here is safe to share a connection across threads). | 3 |
| `write` | A CTAS-equivalent materialization in two table shapes: **narrow** (`ddl/<engine>/narrow.sql`, a single table's own columns) and **wide** (`ddl/<engine>/wide.sql`, the fact table denormalized against every dimension it joins to). Each rep targets its own table (`tpch_write_<shape>_r<rep>`), since a CTAS only succeeds once per table name. | 3 per shape |
| `dml` | 3 cycles of DELETE + INSERT on ~1% of rows against the `write` scenario's rep-0 table, simulating an incremental refresh. Each cycle targets a disjoint ~1% slice (`l_orderkey % 100 = <cycle>`) so cycles don't just churn the same rows; the INSERT re-derives its rows fresh from the base tables (not from the write-target table), so cycles never run out of source rows. | 3 cycles |

**Statistic: median** across reps, pre-declared — no post-hoc selection of
"best run."

DuckHaven-specific handling, both disclosed rather than engineered around:

- Before every `write`/`dml` statement, the session issues
  `SET duckhaven_concurrency = 'single'` first (not itself a measured work
  item), so the write gets the whole agent's memory budget instead of the
  fixed ⅓ ("M") bucket a write statement otherwise falls back to.
- DuckHaven's Iceberg maintenance is **read-only/advisory only** — it
  cannot expire snapshots or rewrite files via the DuckDB iceberg
  extension. The `dml` scenario's repeated DELETE+INSERT will accumulate
  small files/snapshots with **no in-app remediation**. This is a real,
  current product limitation, reported honestly (completes slowly / costs
  more over time), not compacted away before measuring.

## 5. Scale factors

| SF | Approx. size | Scenarios run | Query timeout | Phase |
|---|---|---|---|---|
| SF1 | ~1 GB | All five | 600s | 0 (free/local dry run), then 1 (real Azure) |
| SF10 | ~10 GB | All five | 600s | 2 |
| SF100 | ~100 GB | All five | 1800s | 2 |
| SF300 | ~300 GB | All five | 3600s | 2 |
| SF1000 | ~1 TB | `sequential` + `cold_start` only, plus one scoped write attempt (below) | 14400s (4h) | 3 |

**SF1000 is deliberately scoped down**, not run through the full matrix:
sequential and cold-start reads on all three engines, plus **one**
generously-configured write attempt on **DuckHaven alone** (largest
affordable single agent, `single` concurrency profile, the 4-hour
timeout) — specifically to characterize *where and how it breaks down*,
not to give it a pass/fail spin. The honest outcome (completes slowly /
times out / OOMs) gets reported as-is. This scope, and the €30 total Azure
budget ceiling behind it, is a deliberate project decision, not an
attempt to avoid an unflattering result — the whole point of including
SF1000 at all is to show where DuckDB-based compute stops being
competitive.

A scale factor's entire scenario matrix runs and is torn down (`terraform
destroy`) within a single Azure apply session — Iceberg data does not
survive a destroy/recreate cycle, so a scale factor's work is never split
across sessions.

## 6. Data generation and placement

`tpchgen-cli` (PyPI, a compiled Rust TPC-H generator) generates each scale
factor's Parquet corpus once (`src/tpch_bench/datagen/tpchgen_runner.py`),
chosen over the reference `dbgen` for the throughput SF100–SF1000 needs.

The corpus is published to a **standalone Azure Storage Account**, created
outside the `deploy/terraform` module specifically so it survives every
apply/destroy cycle of the DuckHaven environment (which deletes its own
warehouse storage account on destroy). A `manifest.json` per scale factor
records every file's sha256 and byte size at generation time
(`src/tpch_bench/datagen/corpus.py`); every download and every replication
is verified against it before being used, so a truncated or corrupted
transfer is caught rather than silently loaded.

**DuckHaven and Databricks load from this same generated corpus** —
byte-identical source data, verified by checksum, is the fairness
requirement §7's impartiality rules exist to protect. Since the confirmed
AWS-hosted Databricks trial workspace can't read an Azure Blob location
without cross-cloud federation nobody has set up, the corpus is also
replicated to S3 (same manifest, same checksum verification on the way
out of Azure and the way into S3) so Databricks reads a same-cloud copy.

**Snowflake is the deliberate exception**: it reads its own free,
pre-loaded `SNOWFLAKE_SAMPLE_DATA.TPCH_SFxxx` schemas for SF1/SF10/SF100/
SF1000 — zero load cost, and the same data a real Snowflake evaluation
would reach for first. **SF300 has no pre-loaded Snowflake sample**, so it
is `COPY INTO`'d from the same generated corpus as the other two engines.
This is a **disclosed, intentional asymmetry** (out-of-the-box sample vs.
self-loaded) — not normalized away, because normalizing it away would mean
either loading Snowflake's smaller scale factors unnecessarily (real,
avoidable cost) or fabricating an "unloaded" cost for what most real
Snowflake users never pay.

## 7. Sizing and cost-equivalence

The goal is a fair **$/hr** comparison, not a fair spec comparison — cloud
warehouses abstract the underlying hardware, so tiers below are matched by
*rate*, not by vCPU count. Snowflake↔Databricks tier-size correspondence
follows the precedent set by
[get-select/snowflake-databricks-benchmark](https://github.com/get-select/snowflake-databricks-benchmark)
(referenced for methodology only — that repo has no LICENSE file, so
nothing from it is forked or reused as code):

| Tier | Snowflake | Databricks | DuckHaven target | Used for |
|---|---|---|---|---|
| small | Medium | Small | 2 vCPU / 8 GB | SF1, SF10 |
| medium | Large | Medium | 4 vCPU / 16 GB | SF100 |
| large | X-Large | Large | 8 vCPU / 32 GB | SF300 |
| sf1000_reads | X-Large | Large | 8 vCPU / 32 GB | SF1000 (reads only; the single write attempt is explicitly *not* cost-matched — see §5) |

DuckHaven's `(cpu, memory_gb)` is chosen at run time to match the
resulting $/hr via `GET /admin/agents/compute-options`'s live pricing —
the table above is the starting target, not a fixed spec. The *actual*
$/hr each engine ran at is captured into `cost_facts` and frozen alongside
this document before any timed run at that scale factor, not re-derived
afterward.

Snowflake and Databricks warehouses for each tier are provisioned with
**`AUTO_SUSPEND = 60s`, `AUTO_RESUME = TRUE`** (or the Databricks
Serverless equivalent) — an aggressive, conservative default chosen ahead
of any live account work specifically to bound real-money exposure between
scenario runs, consistent with this project's other cost guardrails (§10).
This is a project default, not a claim about what either vendor
recommends for production use.

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

No errata yet — this document has not been frozen.

## 10. Budget and guardrails

| Item | Rough cost | Notes |
|---|---|---|
| Azure infra baseline while running | ~€4–4.5/day | Postgres + private endpoints + NAT gateway + Container Apps — the non-negotiable floor once elastic compute is exercised. |
| DuckHaven agent compute | ~€0.25/hr (small) to ~€1/hr (large, the SF1000 write attempt) | Live from `GET /admin/agents/compute-options`. |
| Corpus storage | Low single-digit €, total | Cool tier, deleted per scale factor once every load is verified. |
| **Azure total ceiling** | **€30** | Hard stop. |
| Snowflake | $400 trial credit, 30 days | Separate pool, not counted against the €30. |
| Databricks | $400 DBU trial credit, 14 days | Serverless-only, so it stays inside Databricks' own DBU metering with no separate AWS bill on top. |

- `terraform destroy` runs immediately after each scale factor's work
  completes — never left running "just in case."
- An Azure Cost Management budget alert is a tripwire independent of
  manual discipline.
- SF1000 has an explicit go/no-go checkpoint against actual (not
  estimated) spend from `cost_facts` before it starts.
- Snowflake/Databricks warehouses auto-suspend (§7); nothing here is
  billed on an always-on basis by default.

## 11. Reproducibility

What gets published: this document, the full raw `db/results.duckdb`
(work items, per-query results, cost facts, infra events — everything
this benchmark recorded), and a redacted export with any credential-
adjacent fields stripped. Anyone with the same trial-tier access to all
three platforms — or paid access, since none of this depends on trial-only
behavior except the credit pools funding it — can rerun `queries/dialect/`
against the same generated corpus and compare.
