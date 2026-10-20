---
type: how-to
---

# Read query profiles

Open a query's profile to see where its time and memory went, and whether it was slow because it waited or because it
worked.

After a query finishes, the [agent](../compute/agents.md) that ran it records DuckDB's per-operator execution profile
and sends it to the control plane.

## Before you begin

- **You need:** the access to read the query's result. In a workspace with a
  [scoped catalog](../access/scoped-access.md#opening-a-result-needs-the-same-grants-as-running-its-query), that means
  grants on every table the query read.
- **Applies to:** finished queries that return rows. DDL and DML have no profile.
- **Enabled by:** `PROFILING_ENABLED` on the agent, on by default.

## Open a profile

- **In a worksheet**, open the **Profile** tab below the editor after a run: a summary and a collapsible operator tree,
  with a link to the full profile.
- **From History**, click a run. The profile page shows the full SQL and an operator **graph**, with the result at the
  top and the scans at the bottom, since data flows up. Click a node for its detail. The page also ranks the most
  expensive operators and lists what it detected in a **diagnostics** panel, each linking to its node.

## What it shows

- A **summary strip** — latency, CPU time, rows returned, result size, peak memory, the reserved memory and CPU the
  query ran under, spill-to-disk, bytes read/written, and the two waiting measures below.
- **Per-operator metrics** — rows read to rows produced, bytes, a time-share bar, and the operator's join conditions,
  filters, and group keys.
- **Named operators** — each entry in the ranked list and the diagnostics identifies itself: *Scan analytics.events*,
  *Inner join on o_custkey = c_custkey*, *Group by c_name*. You can see which table is slow without opening it.
- **Share of operator time** — a rollup by kind of operator (scans, joins, aggregates, sorts) answering "where did the
  time go" in one line.
- **Inefficiency highlights** computed from the profile — spills (worth a larger reservation), scan blow-ups (a scan
  reading far more rows than **it** emitted, the mark of a filter that never reached the reader), bad cardinality
  estimates (actual far from the optimizer's estimate), and time hotspots. The dedicated page also ranks the most
  expensive operators.

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

!!! note "Why a scan blow-up is judged against the scan, not the query"
    An aggregate is *supposed* to reduce a lot of rows to a few, so comparing a scan's input against the query's final
    row count marks every `GROUP BY` as a defect. The badge compares a scan's input to its own output instead, and
    divides out the per-thread double count described below, so it fires on scans that genuinely read more than they
    needed rather than on ordinary aggregation.

!!! note "Rows read is corrected before it is shown"
    DuckDB reports a scan's row count *per thread*, counting the whole relation once for every thread that took part,
    so the raw figure grows with the size of the agent the query happened to run on — the same scan of a 200,000-row
    file reports 200,000 on one thread and 1,600,000 on eight. DuckHaven divides by the reservation's thread count
    before showing it, so **Rows read** is what the scan actually read. Profiles captured before that thread count was
    recorded are shown uncorrected.

!!! warning "What these do not tell you"
    DuckDB 1.5.5 reports **no byte-pruning figure and no row-group counters**, so DuckHaven shows none and never
    labels anything "bytes pruned". For Iceberg tables it reports only files read — not how many were skipped — so a
    low count is not by itself evidence that pruning worked. DuckHaven also does not show DuckDB's *rows scanned* for
    file-based scans: it is accurate for a scan of a local table but not for Parquet or Iceberg, where it stays the
    same however few columns were projected and however selective the filter was, so a "rows read versus rows
    returned" ratio built on it would mislead for exactly the storage DuckHaven reads.

## If it goes wrong

### There is no profile

The statement was DDL or DML, profiling is off on the agent (`PROFILING_ENABLED=false`, see the [Agent
configuration](../reference/agent-configuration.md)), or capturing it failed. Capture is best effort: a failure leaves
the query without a profile rather than failing it.

### "You don't have access to the data this query read, so its profile can't be shown."

A [scoped catalog](../access/scoped-access.md) refuses the profile, as it would the result. Ask for a grant on the
tables the query read.

### The profile shows a long admission wait

The agent was busy, not the query expensive. See [When the agent is full](query-execution.md#when-the-agent-is-full)
and [Scaling compute](../compute/scaling.md).

## Related

- [Query execution](query-execution.md) — how memory is reserved for a query.
- [Run queries](run-queries.md) — the worksheet and History.
