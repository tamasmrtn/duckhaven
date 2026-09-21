# Lakehouse maintenance

The **maintenance advisor** is the DuckHaven service that periodically scans every table in every catalog — both
[catalog kinds](catalogs.md#catalog-kinds) — computes a health score, and raises recommendations for the maintenance
each table needs — compaction, snapshot expiration, manifest
rewrites, and orphan cleanup. It runs as a background loop in the control plane and is configured in
**Admin → Maintenance**; the loop itself is gated by an
[environment flag](../reference/configuration.md#maintenance-advisor).

!!! note "What it can run depends on the catalog kind"
    The advisor detects, scores, and recommends for both kinds. Whether it can also *act* is the extension's answer,
    not a policy: DuckDB's `ducklake` extension performs compaction, snapshot expiry and cleanup, so those
    recommendations carry an [Apply](#applying-maintenance) button. Its `iceberg` extension is read-only for
    maintenance — it cannot expire snapshots, rewrite data files or remove orphans — so an Iceberg recommendation
    carries the equivalent command for an external engine instead. Adding a second write engine (Spark, PyIceberg) to
    the stack for that is a bigger change than the advisor.

## What it can recommend, per catalog kind

The findings are the same for both [catalog kinds](catalogs.md#catalog-kinds) — too many small files is too many small
files — but the fix is not. DuckDB's `ducklake` extension can run compaction, snapshot expiry and orphan cleanup; its
`iceberg` extension cannot run Iceberg's equivalents, which is why every recommendation for an Iceberg table names an
external engine.

A [DuckLake](ducklake.md) catalog's recommendations therefore name `ducklake_*` commands DuckDB can run, with two
differences worth knowing: snapshot expiry is catalog-level only (DuckLake's `expire_older_than` has global scope), and
manifest rewrites do not apply at all, because manifests are an Iceberg structure with no DuckLake counterpart.

Snapshot metrics for a DuckLake table are **catalog-scoped**, because a DuckLake snapshot is a commit against the
whole catalog rather than one table. Every table in a DuckLake catalog therefore reports the same snapshot count and
age. That is the honest number and the right one here: the expiry rule scores on the oldest snapshot's age, and
`ducklake_expire_snapshots` is catalog-level anyway, so the metric and the fix are at the same grain.

!!! note "DuckHaven can run DuckLake's, and only DuckLake's"
    A DuckLake recommendation carries an **Apply** button; an Iceberg one does not, and says so. That asymmetry is
    the extension's, not a policy choice — see [Applying maintenance](#applying-maintenance) below.

## Applying maintenance

Where the catalog kind allows it, a recommendation has an **Apply** button beside **Copy**. Pressing it dispatches the
same command the card displays — rendered once, in one place, so what you read and what runs cannot drift.

It requires the `maintenance:manage` permission as well as `writer` on the workspace. The button sits on a page any
member can open, but the action rewrites or deletes data files, which is operator-grade whoever is looking at it.

Two of the five verbs act on the **whole catalog** rather than one table: expiring snapshots and cleaning up old files
have catalog-wide scope in DuckLake. Applying either from one table's page affects every table in that catalog, and
the confirmation says so before you continue.

DuckHaven refuses to start an apply when the recommendation has been dismissed, when the catalog is mid
[storage migration](catalogs.md#storage-migration), or when another apply is already running on the same catalog.
Cleanup is always bounded by the policy's snapshot retention — never `cleanup_all`, which would delete files that
older snapshots still reference and that [time travel](tables.md) is still entitled to read.

A successful apply does **not** mark the recommendation resolved. The table is re-probed, and the next scan resolves
it only if the condition actually cleared: a verb that ran is not the same as a problem that went away. The
recommendation carries the outcome in the meantime, and the query that ran it appears in
[History](../operations/monitoring.md#query-history-and-audit-log) under your name.

!!! note "Manual, in this release"
    There is no scheduled or automatic apply. Unattended file deletion needs a maintenance-window concept — when it
    may run, what it locks, how it backs off — that this release does not have.

## Health score

Each scanned table gets a score from **0 to 100**, grouped into three bands:

| Band | Score | Meaning |
|---|---|---|
| Healthy | 90–100 | No action needed. |
| Fair | 70–89 | Minor degradation; review. |
| Needs attention | 0–69 | One or more dimensions well past target. |

The score is a weighted average of four dimensions, each a linear function of one metric against the active policy's
thresholds. A dimension whose metric cannot be measured is dropped and the remaining weights are renormalized, so a
partial scan still scores over what it measured.

| Dimension | Weight | Metric |
|---|---|---|
| Fragmentation | 35% | Share of data files below the target file size. |
| Snapshot hygiene | 25% | Oldest snapshot age against the retention target. |
| Metadata health | 20% | Manifest count relative to data files. |
| Storage efficiency | 20% | Estimated orphaned bytes relative to total data bytes. |

Every score is returned with its per-dimension breakdown — raw value, sub-score, and a one-line explanation — so the UI
never shows a bare number.

Namespace, workspace, and deployment scores are **data-byte-weighted** averages of their table scores (a 1 TB table
outweighs a 1 MB one). Each rollup also reports the table count and the number of tables needing attention, so a few
small unhealthy tables are not hidden behind one healthy large one.

## Recommendations

A recommendation is raised when a metric crosses its threshold. Each carries a `kind`, a `severity` (`warning` or
`critical`, from how far past threshold the metric is), a `confidence`, a generated `rationale`, an `estimated_impact`,
and a `remediation` command for an external engine.

| Kind | Fires when | Confidence | Remediation |
|---|---|---|---|
| `compact_small_files` | Small-file ratio above threshold | high | `rewrite_data_files` |
| `expire_snapshots` | Oldest snapshot age past the retention target | high | `expire_snapshots` |
| `rewrite_manifests` | Manifest count high relative to data files | high | `rewrite_manifests` |
| `cleanup_orphans` | Orphaned bytes above threshold | low | `remove_orphan_files` |
| `investigate_growth` | Storage grew abnormally over the trend window | medium | Review writers / partitioning |

Confidence is data-driven: recommendations computed from complete metadata are `high`; the orphan estimate is `low`.

Recommendations are a living feed. When a later scan shows the condition has cleared, the recommendation
auto-**resolves**. A user can **dismiss** one; it stays suppressed until the metric worsens again.

## Scanning

The scanner walks the catalog each cycle and probes tables through the same agent dispatch path as user queries, using
the DuckDB `iceberg` extension over the attached Polaris catalog. To bound cost on large deployments:

- **Cadence** — `off`, `hourly`, or `daily`, set by policy.
- **Incremental** — tables whose current snapshot is unchanged since the last sample are skipped.
- **Two-tier** — the cheap metadata probe runs every due cycle; the expensive orphan/`glob` scan runs on a slower
  cadence (weekly by default). For a DuckLake catalog the cheap tier already carries exact sizes, so only its orphan
  count waits for the slow tier.
- **Budget** — at most `max_tables_per_cycle` tables per cycle, covered round-robin so no single cycle scans everything.

A cycle with no connected agent is skipped, not failed. Per-table probe failures degrade that table's affected metrics
to null rather than failing the cycle.

## Configuration

Maintenance exposes two controls in **Admin → Maintenance**:

1. **Autonomous scanning** — on/off and frequency (`Off` / `Hourly` / `Daily`).
2. **Maintenance profile** — `Conservative`, `Balanced` (default), or `Aggressive`. The profile resolves the full
   threshold bundle that defines both the score and recommendation sensitivity; a more aggressive profile flags
   problems sooner.

An **Advanced** section (collapsed by default) exposes the resolved threshold values for individual override.

Balanced defaults: 256 MB target file size, small-file ratio warns above 30%, 7-day snapshot retention, orphan share
warns above 5% of data, daily scan with the orphan/storage tier weekly.

See the [configuration reference](../reference/configuration.md#maintenance-advisor) for the scanner environment
variables.

## Limitations

- **In-app apply is DuckLake-only, and manual.** An Iceberg recommendation can only name an external engine, and
  neither kind applies on a schedule. See [Applying maintenance](#applying-maintenance).
- **Orphan detection is an estimate.** It compares files listed under a table's data and metadata directories against
  files referenced by the *current* snapshot's metadata. Files referenced only by older snapshots (still valid for
  time travel) can appear orphaned, and there is no age window — DuckDB exposes no file modification time — so these
  recommendations are flagged low confidence and are an estimate to investigate, never an instruction to delete.
- **File sizes are estimated on the deep tier, for Iceberg.** DuckDB's `iceberg` extension does not expose a data-file
  size column, so the deep scan reads Parquet footers to size files; on very wide tables it samples a bounded subset and
  scales the total, so the small-file ratio and average are estimates. A [DuckLake](ducklake.md) catalog has no such
  limit — file sizes are columns in its catalog database, so its sizes and small-file ratio are exact and available on
  the cheap tier, and its orphan count is the exact list of files DuckLake has scheduled for deletion rather than a
  `glob` comparison.
- **Single scanner per cluster.** Only one scan cycle runs at a time across the whole deployment. With multiple API
  replicas the loop coordinates through a Postgres advisory lock (leader election), so it is safe to leave
  `MAINTENANCE_SCANNER_ENABLED` on every replica — exactly one wins each tick. See
  [High availability](../deployment/high-availability.md).
- **No per-table policies.** Thresholds are deployment-wide. Per-table and per-namespace overrides are not yet
  supported.

## Where to find it

- **Lakehouse Health** (per workspace) — overall score, dimension breakdown, tables needing attention, and the
  recommendation feed.
- **Catalog table detail** — a Health panel beside the snapshot history: the table's score, factor breakdown,
  storage-growth trend, and recommendations.
- **Admin → Maintenance** — the policy form, last-scan status, and a manual **Scan now** trigger.

## Related

- [Tables & Iceberg](tables.md) — snapshots, data files, and metadata.
- [Metadata](metadata.md) — the per-table facts the advisor builds on.
- [Configuration](../reference/configuration.md#maintenance-advisor) — scanner environment variables.
