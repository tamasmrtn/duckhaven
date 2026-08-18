# Lakehouse maintenance

The **maintenance advisor** is the DuckHaven service that periodically scans every Iceberg table, computes a health
score, and raises recommendations for the maintenance each table needs — compaction, snapshot expiration, manifest
rewrites, and orphan cleanup. It runs as a background loop in the control plane and is configured in
**Admin → Maintenance**; the loop itself is gated by an
[environment flag](../reference/configuration.md#maintenance-advisor).

!!! note "Recommend-only in this release"
    The advisor detects, scores, and recommends. It does **not** execute maintenance. DuckHaven runs Iceberg through the
    DuckDB `iceberg` extension, which is read-only for maintenance — it cannot expire snapshots, rewrite data files, or
    remove orphans. Rather than add a second write engine (Spark, PyIceberg) to the stack, DuckHaven ships the advisor
    now and will add in-app apply once the extension supports these operations natively. Every recommendation includes
    the equivalent command to run in an external Iceberg engine.

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
  cadence (weekly by default).
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

- **No in-app apply.** Recommendations are advisory; run the remediation command in an external Iceberg engine. See the
  recommend-only note above.
- **Orphan detection is an estimate.** It compares files listed under a table's data and metadata directories against
  files referenced by the *current* snapshot's metadata. Files referenced only by older snapshots (still valid for
  time travel) can appear orphaned, and there is no age window — DuckDB exposes no file modification time — so these
  recommendations are flagged low confidence and are an estimate to investigate, never an instruction to delete.
- **File sizes are estimated on the deep tier.** DuckDB's `iceberg` extension does not expose a data-file size column,
  so the deep scan reads Parquet footers to size files; on very wide tables it samples a bounded subset and scales the
  total, so the small-file ratio and average are estimates.
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
