# Agent monitoring

Open **Compute** and click an agent to reach its detail page. The **Monitoring** tab answers the questions an operator
brings to it — *was it saturated? did memory spike? how much of the time it was up was it actually working?* — and
leads from each answer to the queries behind it. It reads top to bottom: what the agent is doing now, what the chosen
range added up to, every series on one time axis, and then the queries themselves.

## Live now

A row of tiles shows the present moment, from the agent's own 2-second samples:

- **Status**, with how long ago the last sample arrived.
- **Executing** — statements running right now. An open SQL session with nothing running (a dbt or BI connection
  waiting for its next statement) holds an admission slot but is idle, so it appears as *+N idle connections* beside
  the count, never in it.
- **Queued** — work waiting for an admission slot, plus any statement waiting for memory to grow into.
- **CPU** and **Memory**, as a share of the agent's size ("of 4 vCPU", "of 16 GB").

A value an older agent cannot measure reads **—**, never 0. The **Compute** list shows the same live figures for every
connected agent, with a sparkline of the last few minutes of CPU, so a busy agent stands out without opening each one.

## The range

The range control offers 1, 3, 8, 12 and 24 hours and 3 and 7 days (the week that is retained). **Drag across any
chart** to zoom to that stretch; **Reset zoom** returns to the preset. The range lives in the page's URL, so a zoomed
view of an incident can be bookmarked and shared. The bucket size adapts to the range — one minute for an hour, up to
two hours for a week — and is shown beside the control, together with how fresh the resource data is.

Beneath it, one line sums up the range: **Up**, **Busy** (share and duration), **Idle**, **Queries** finished (and how
many failed), the **p95 wait** before a query started running, and **Peak memory**.

## The charts

Every chart shares one time axis. Stretches when the agent was not running are shaded across all of them, and the
bucket still in progress at the right-hand edge is outlined, because its numbers are still moving.

**Timeline** shows where the agent's time went in each bucket: **busy** (at least one query running), **idle** (up,
nothing running — including time held open by an idle SQL session), **starting**, **not running**, or **no record**
(before the agent had a lifecycle trail, which is deliberately not drawn as downtime).

**Queries** counts the queries that finished in each bucket, stacked by outcome. **SQL error** is a mistake in the query
itself — a typo, a missing table, a bad cast — which is the author's to fix. **Failed** is the platform's: a full queue,
an out-of-memory failure, no compute becoming available, a timeout. The causes behind *Failed* are listed above the
chart and in each bar's tooltip, because each points at a different fix.

**Concurrency** shows the average number of queries in each state over each bucket, stacked: **running**, **waiting to
run** (the agent was up but had no free slot, or was planning the query), and **waiting for compute** (an elastic agent
was still starting). The waiting layers are the saturation signal: sustained waiting means the agent is too small or
too busy. The dashed line is the most queries running at any single instant in the bucket, and the chart's subtitle
gives the p95 wait before running.

**CPU** and **Memory** each draw the bucket's average as a stepped line and its peak as a band above it, as a share of
the agent's size. CPU's peak is the highest 2-second reading. Memory's peak is the highest level reached, including
between samples, so a spike that starts and ends inside one sample interval still shows. A red **OOM** marker flags
a bucket where the kernel killed a process for memory or a query failed out of memory. A gap means nothing was
measured, never a measured zero.

**Click a bar** in any chart to list the queries in that bucket.

## The queries

The table under the charts lists every query that was running or waiting during the range — or the clicked bucket —
with how long it waited, how long it ran, and what it cost as DuckDB reported it: **peak memory**, **CPU time**,
**spill** to disk, and data read. Sort it by any of these to find the query behind a spike. Hover a duration to split it
into wait and run time. Listing spans workspaces, so it needs the same cross-workspace query permission as the global
History view.

## How each number is measured

Everything with a start and an end is measured exactly, from timestamps, rather than sampled:

| Figure | How it is computed |
|---|---|
| Busy | The total length of time at least one query was running, while the agent was up. Two queries side by side count once. |
| Up / idle | From the agent's lifecycle trail; idle is up minus busy. |
| Concurrency averages | Query-seconds in each state divided by the bucket's seconds (Little's law): the time-weighted average number of queries in that state. |
| Peak running | The true maximum overlap of running queries within the bucket. |
| Wait | From a query's submission to when it started running: the admission queue, planning, and any time compute was starting. |
| CPU | The agent's cgroup CPU counter, averaged over each 2-second interval, then over the bucket. |
| Memory | The agent's cgroup memory use against its limit. Its peak is tracked between samples (a 250 ms poll plus the kernel's own `memory.peak`). |

Because these are exact, the same history reads the same at every zoom level: a busy share does not change when you
widen or narrow the range. Idle time held open by an SQL session still counts as idle, although the idle timeout cannot
reclaim it while the session stays open.

!!! note "What sampling still cannot see"
    CPU and memory can only be sampled. CPU is a counter, so no work is lost, but a burst shorter than 2 seconds is
    averaged into its interval and its peak reads lower than it was. The per-query **peak memory** and **CPU time** in
    the table are DuckDB's own figures and are exact for each query.

## Where the data comes from

Agents sample themselves every 2 seconds. Those samples feed a short in-memory buffer for the live tiles and the minute
still in progress, and are rolled up to **one row per agent per minute** in Postgres. The query figures come from the
query records themselves, and exclude the same internal queries the History page does. Agent lifecycle transitions are
kept as an append-only trail.

If an agent goes away without a clean disconnect — the API replica holding its connection crashed, or it lost its
network — a presence sweeper notices its lapsed heartbeat and closes its trail at the last moment it was seen, so the
timeline shows it as not running rather than up. The minute rollup and the lifecycle trail are retained for
`AGENT_METRICS_RETENTION_HOURS` (default one week); the trail always keeps each agent's most recent earlier event, so
an agent that has been connected for longer than that still has a timeline.

This is deliberately DuckHaven's own storage rather than the [Prometheus](monitoring.md#prometheus-metrics) or
[tracing](tracing.md) pipelines below. Both of those are export-only and off by default; a built-in product page that
renders blank unless you deployed a collector would be the wrong default.

!!! note "Growth"
    The `queries` table that backs the query figures and the audit log is **not** currently pruned — it grows for the
    life of the deployment.

## Related

- [Monitoring](monitoring.md) — query history and the audit log, Prometheus metrics, alert rules and a Grafana
  dashboard.
- [Agents](../concepts/agents.md) and [Elastic compute](../concepts/elastic-compute.md) — what is being monitored.
- [Scaling compute](scaling.md) — what to do when an agent is saturated.
