# Read query profiles

After a query finishes, the executing [agent](../concepts/agents.md) captures DuckDB's per-operator execution profile
and ships it to the control plane. There are two ways to view it.

## Where to find it

- **Worksheet → Profile tab** — an inline summary and a collapsible operator tree for the query you just ran, with a
  link to the full profile.
- **Dedicated profile page** — reached by clicking any row in query history. It shows the full query SQL in a
  capped-height panel below the header (with an expand button for statements that don't fit), and an interactive
  operator **graph** (result on top, scans at the bottom; data flows up); click a node for its detail.

## What it shows

- A **summary strip** — latency, CPU time, rows returned, result size, peak memory, the reserved memory and CPU the
  query ran under, spill-to-disk, bytes read/written, and the two waiting measures below.
- **Per-operator metrics** — rows scanned to produced, bytes, a time-share bar, and the operator's join conditions,
  filters, and group keys.
- **Named operators** — each entry in the ranked list and the diagnostics identifies itself: *Scan analytics.events*,
  *Inner join on o_custkey = c_custkey*, *Group by c_name*. You can see which table is slow without opening it.
- **Share of operator time** — a rollup by kind of operator (scans, joins, aggregates, sorts) answering "where did the
  time go" in one line.
- **Inefficiency highlights** computed from the profile — spills (worth a larger reservation), scan blow-ups (a scan
  reading far more rows than returned), bad cardinality estimates (actual far from the optimizer's estimate), and time
  hotspots. The dedicated page also ranks the most expensive operators.

## Waiting versus working

Three separate measurements, deliberately not stacked into a single bar:

- **Admission wait** — how long the statement queued for its memory and thread reservation before it could start.
  DuckHaven's own measurement. A large value means the query was slow because the agent was busy, not because the
  query was expensive.
- **Blocked** — time DuckDB's threads spent parked waiting on I/O or on another operator instead of working.
- **CPU time** — DuckDB's own counter, summed across threads.

!!! note "These do not add up to latency"
    They overlap, and CPU time is a sum across threads so it can exceed wall clock on a parallel plan. Read them as
    three answers to "was it waiting or working", not as a breakdown of where the wall clock went. **Share of operator
    time** is likewise a share of summed operator self time, not of latency, for the same reason.

## Scan effectiveness

For a selected scan the profile reports what DuckDB actually measured:

- **Files read** — how many data files the scan opened. Where DuckDB reports a ratio (a partitioned Parquet read), it
  shows files read of files considered.
- **Rows produced** — rows the scan emitted.
- **Filters pushed down** — the predicates the reader applied itself rather than leaving to a later operator.

!!! warning "What these do not tell you"
    DuckDB 1.5.5 reports **no byte-pruning figure and no row-group counters**, so DuckHaven shows none and never
    labels anything "bytes pruned". For Iceberg tables it reports only files read — not how many were skipped — so a
    low count is not by itself evidence that pruning worked. DuckHaven also does not show DuckDB's *rows scanned* for
    file-based scans: it is accurate for a scan of a local table but not for Parquet or Iceberg, where it stays the
    same however few columns were projected and however selective the filter was, so a "rows read versus rows
    returned" ratio built on it would mislead for exactly the storage DuckHaven reads.

## Notes

Profiling is on by default and best-effort: a capture failure yields no profile rather than failing the query, and
DDL/DML statements carry no profile. To disable it, set `PROFILING_ENABLED=false` on the agent (see the
[Agent reference](../reference/agent-reference.md)).

## Related

- [Query execution](../concepts/query-execution.md) — how reservations are sized.
