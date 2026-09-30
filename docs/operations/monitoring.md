# Monitoring

DuckHaven gives every [agent](../concepts/agents.md) its own monitoring page, and records a full query audit trail on
the **History** page.

## Per-agent monitoring

Open **Compute** and click an agent to reach its detail page. The **Monitoring** tab answers the questions an operator
brings to it — *was it saturated? did memory spike? how much of the time it was up was it actually working?* — and
leads from each answer to the queries behind it. It reads top to bottom: what the agent is doing now, what the chosen
range added up to, every series on one time axis, and then the queries themselves.

### Live now

A row of tiles shows the present moment, from the agent's own 2-second samples:

- **Status**, with how long ago the last sample arrived.
- **Executing** — statements running right now. An open SQL session with nothing running (a dbt or BI connection
  waiting for its next statement) holds an admission slot but is idle, so it appears as *+N idle connections* beside
  the count, never in it.
- **Queued** — work waiting for an admission slot, plus any statement waiting for memory to grow into.
- **CPU** and **Memory**, as a share of the agent's size ("of 4 vCPU", "of 16 GB").

A value an older agent cannot measure reads **—**, never 0. The **Compute** list shows the same live figures for every
connected agent, with a sparkline of the last few minutes of CPU, so a busy agent stands out without opening each one.

### The range

The range control offers 1, 3, 8, 12 and 24 hours and 3 and 7 days (the week that is retained). **Drag across any
chart** to zoom to that stretch; **Reset zoom** returns to the preset. The range lives in the page's URL, so a zoomed
view of an incident can be bookmarked and shared. The bucket size adapts to the range — one minute for an hour, up to
two hours for a week — and is shown beside the control, together with how fresh the resource data is.

Beneath it, one line sums up the range: **Up**, **Busy** (share and duration), **Idle**, **Queries** finished (and how
many failed), the **p95 wait** before a query started running, and **Peak memory**.

### The charts

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

### The queries

The table under the charts lists every query that was running or waiting during the range — or the clicked bucket —
with how long it waited, how long it ran, and what it cost as DuckDB reported it: **peak memory**, **CPU time**,
**spill** to disk, and data read. Sort it by any of these to find the query behind a spike. Hover a duration to split it
into wait and run time. Listing spans workspaces, so it needs the same cross-workspace query permission as the global
History view.

### How each number is measured

Everything with a start and an end is measured exactly, from timestamps, rather than sampled:

| Figure | How it is computed |
|---|---|
| Busy | The total length of time at least one query was running, while the agent was up. Two queries side by side count once. |
| Up / idle | From the agent's lifecycle trail; idle is up minus busy. |
| Concurrency averages | Query-seconds in each state divided by the bucket's seconds (Little's law) — the same "load" Snowflake reports for its warehouses. |
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

### Where the data comes from

Agents sample themselves every 2 seconds. Those samples feed a short in-memory buffer for the live tiles and the minute
still in progress, and are rolled up to **one row per agent per minute** in Postgres. The query figures come from the
query records themselves, and exclude the same internal queries the History page does. Agent lifecycle transitions are
kept as an append-only trail.

If an agent goes away without a clean disconnect — the API replica holding its connection crashed, or it lost its
network — a presence sweeper notices its lapsed heartbeat and closes its trail at the last moment it was seen, so the
timeline shows it as not running rather than up. The minute rollup and the lifecycle trail are retained for
`AGENT_METRICS_RETENTION_HOURS` (default one week); the trail always keeps each agent's most recent earlier event, so
an agent that has been connected for longer than that still has a timeline.

This is deliberately DuckHaven's own storage rather than the [Prometheus](#prometheus-metrics) or
[tracing](tracing.md) pipelines below. Both of those are export-only and off by default; a built-in product page that
renders blank unless you deployed a collector would be the wrong default.

!!! note "Growth"
    The `queries` table that backs the query figures and the audit log is **not** currently pruned — it grows for the
    life of the deployment.

## Query history and audit log

Every query a member runs is recorded on the **History** page (newest first), excluding internal queries such as
table-sample previews. Each member sees the history for the workspace they are in, with a row click opening the
query's profile. A refresh button re-fetches the list on demand, and a filter by **agent** is open to any member —
narrowing to one agent reveals nothing about the workspace's own queries that the member could not already see.

Administrators get an extra **This workspace / All workspaces** toggle on the same page. Switching to **All
workspaces** turns History into the global audit log: every query across every workspace, with a Workspace column.
Administrators also get a filter by **user**, which — unlike the agent filter — works within the current workspace
view as well as the cross-workspace one, since it reveals who ran a query and stays admin-only either way. Each
record captures the **user** who ran it (the human or [service account](../guides/service-accounts.md) — shown in the
**User** column), the SQL, status, row count, duration, result size, and any error. There is no separate audit table;
the audit log is the query record itself.

## Prometheus metrics

Each API replica exposes a Prometheus text-exposition endpoint at **`GET /api/metrics`**. It
re-exports the same data the admin console shows — live agent utilization, the query audit
log, and the maintenance scanner — plus standard HTTP, database-pool, and process telemetry,
so you can alert on saturation and failure rates instead of polling the REST API.

The endpoint is **unauthenticated**, exactly like `/api/healthz` and `/api/readyz`: Prometheus
scrapers carry no session cookie, and DuckHaven already assumes no public ingress. Keep it on
the internal network. Set `METRICS_ENABLED=false` (see the
[configuration reference](../reference/configuration.md#observability)) to remove it entirely.

DuckHaven does **not** ship a bundled Grafana container — deploying the dashboard stack is left
to you. The metric reference, scrape config, and a starter dashboard below are everything you
need to wire it into an existing Prometheus + Grafana.

### Scrape configuration

Scrape **each replica directly** (not through the load balancer) so per-replica series stay
distinct and aggregations are correct. A single-node install has one target.

```yaml
scrape_configs:
  - job_name: duckhaven
    metrics_path: /api/metrics
    static_configs:
      - targets:
          - api-1.internal:8000
          - api-2.internal:8000   # only under the HA topology
```

### Metric reference

Counters and histograms are per-replica (carry a `replica_id` label). Aggregate them with
`sum`/`rate` across replicas — every underlying event (a query completing, a request being
served) happens on exactly one replica, so summing never double-counts. Counter series carry
the conventional `_total` suffix in the exposition (e.g. `duckhaven_queries_total`).

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `duckhaven_queries_submitted_total` | counter | `replica_id` | User queries accepted for dispatch (excludes internal/maintenance queries). |
| `duckhaven_queries_total` | counter | `replica_id`, `status` | User queries reaching a terminal state (`done`/`failed`/`cancelled`). |
| `duckhaven_query_failures_total` | counter | `replica_id`, `reason` | Failed user queries by classified cause. `sql_error` is a mistake in the query itself (parser, binder, catalog, conversion errors); the other reasons (`queue_full`, `out_of_memory`, `no_compute`, `timeout`, …) are the platform's. |
| `duckhaven_query_duration_seconds` | histogram | `replica_id` | Duration of completed (`done`) user queries. |
| `duckhaven_query_result_bytes` | histogram | `replica_id` | Result size of completed (`done`) user queries. |
| `duckhaven_query_queue_wait_seconds` | histogram | `replica_id` | Time a user query waited in the agent admission queue before running. |
| `duckhaven_query_queue_rejected_total` | counter | `replica_id`, `reason` | User queries rejected by agent admission control (`reason`: `queue_full`/`queued_timeout`). |
| `duckhaven_http_requests_total` | counter | `replica_id`, `method`, `route`, `status` | REST API requests, keyed by route template. |
| `duckhaven_http_request_duration_seconds` | histogram | `replica_id`, `method`, `route` | REST API request latency. |
| `duckhaven_polaris_requests_total` | counter | `replica_id`, `operation`, `status` | Requests to Apache Polaris (Iceberg REST + management). `status` is the HTTP code, or `error` for transport failures. |
| `duckhaven_ducklake_queries_total` | counter | (same) | Metadata queries to a DuckLake catalog database — the counterpart to the Polaris rows above, so a slow browse appears in one or the other by kind. `status` is `ok`, `error`, or `empty` (nothing has attached it yet). |
| `duckhaven_ducklake_query_duration_seconds` | histogram | (same) | Latency of the above. |
| `duckhaven_polaris_request_duration_seconds` | histogram | `replica_id`, `operation` | Latency of requests to Apache Polaris. |
| `duckhaven_agent_up` | gauge | `replica_id`, `agent_id`, `agent_name` | `1` for each agent with a recent sample owned by this replica. |
| `duckhaven_agent_cpu_percent` | gauge | (same) | Agent CPU utilization. |
| `duckhaven_agent_memory_percent` | gauge | (same) | Agent memory utilization. |
| `duckhaven_agent_running_queries` | gauge | (same) | Admission slots in use on the agent, **including idle held SQL sessions**. For "queries actually running" use `duckhaven_agent_executing_queries`. |
| `duckhaven_agent_executing_queries` | gauge | (same) | Statements running on the agent right now; an idle held SQL session is not counted. Absent for agents too old to report it. |
| `duckhaven_agent_idle_sessions` | gauge | (same) | Open SQL sessions on the agent that are not running a statement. |
| `duckhaven_agent_memory_peak_percent` | gauge | (same) | Highest memory use reached during the agent's last 2-second sample interval, including spikes between samples. |
| `duckhaven_agent_cpu_seconds_total` | counter | (same) | CPU time consumed by the agent's cgroup. `rate()` of it is exact CPU use over any window, however short the bursts inside it. |
| `duckhaven_agent_oom_kills_total` | counter | (same) | Processes the kernel's OOM killer has killed in the agent's cgroup. As a counter, no scrape interval can miss one. |
| `duckhaven_agent_queued_queries` | gauge | (same) | Queries queued on the agent. |
| `duckhaven_agent_growth_waiting` | gauge | (same) | Statements parked waiting for memory to grow into — already admitted, unlike `queued_queries`. A steady non-zero value alongside near-zero `duckhaven_agent_cpu_percent` means statements are waiting on each other rather than on work, and is the signal to look at. |
| `duckhaven_agent_estimates_abandoned` | gauge | (same) | Query-cost estimates the agent gave up on because DuckDB's planner stopped responding. Each one costs the agent a worker thread and a CPU core until it restarts, and the affected query is sized from a default rather than its real estimate — so this should stay flat. A rising value on one agent is a reason to restart it. |
| `duckhaven_agent_active_profile_info` | gauge | (same) + `profile` | Active concurrency profile (value always `1`). |
| `duckhaven_agents` | gauge | `provider`, `lifecycle` | Elastic agents by backend and lifecycle state. Reported by the reap leader only, so it is a cluster-wide count — do not sum it across replicas. |
| `duckhaven_agent_provisions_total` | counter | `replica_id`, `provider`, `runtime`, `outcome` | Elastic provisioning attempts (`outcome`: `success`/`failure`). |
| `duckhaven_agent_provisioning_seconds` | histogram | `replica_id`, `provider`, `runtime` | Time the backend took to create the instance, not the full cold start. Successes only. |
| `duckhaven_agents_reaped_total` | counter | `replica_id`, `reason` | Elastic agents torn down by the reaper (`reason`: `idle`/`max_lifetime`/`provisioning_timeout`/`orphan`/`dead_row`). |
| `duckhaven_db_pool_size` | gauge | `replica_id`, `pool` | Configured connection-pool size. `pool` is `main` or `ducklake`, the latter only once a DuckLake catalog has been browsed. |
| `duckhaven_db_pool_checked_out` | gauge | (same) | Connections checked out. Saturating the `ducklake` pool reads as slow browsing, not slow queries. |
| `duckhaven_db_pool_overflow` | gauge | (same) | Connections beyond the configured pool size. |
| `duckhaven_maintenance_last_scan_timestamp_seconds` | gauge | — | Unix time of the last completed maintenance scan cycle. |
| `duckhaven_maintenance_open_recommendations` | gauge | `severity` | Open maintenance recommendations by severity. |
| `duckhaven_maintenance_table_health_samples` | gauge | — | Total table-health samples recorded. |
| `process_*`, `python_*` | counter/gauge | — | Standard process and Python runtime metrics. |

In-flight `queued`/`running` query counts are exposed as the per-agent gauges
(`duckhaven_agent_running_queries` / `_queued_queries`); `duckhaven_queries_total` records
terminal outcomes. This is the Prometheus-idiomatic split — counters for events, gauges for
instantaneous state.

Two signals deserve a callout because they catch failure modes a generic query-failure count
would hide:

- **Queue admission** — `duckhaven_query_queue_wait_seconds` is the time queries spend waiting
  for an agent slot, and `duckhaven_query_queue_rejected_total` counts queries the agent turned
  away once `MAX_QUEUE_DEPTH` / `QUEUED_TIMEOUT_S` were hit. A rising wait time or any
  rejections mean the fleet is saturated — add an agent or raise its slot count. (Rejections
  also show up under `duckhaven_queries_total{status="failed"}`; this counter is the specific
  breakdown.)
- **Catalog dependency health** — `duckhaven_polaris_requests_total` and `duckhaven_ducklake_queries_total`,
  each with its `_duration_seconds` companion. Watch whichever kinds are deployed.
  surface the Iceberg catalog's error rate and latency. Alert on a non-zero rate of
  `status="error"` (or 5xx) here to catch catalog-layer degradation before it manifests as
  mysterious query failures.
- **Elastic supply** — `duckhaven_agent_provisions_total{outcome="failure"}` and
  `duckhaven_agents_reaped_total{reason="provisioning_timeout"}` both mean users are waiting for
  compute that never arrives, which surfaces to them as a query that simply never starts.
  `duckhaven_agents_reaped_total{reason="orphan"}` or `{reason="dead_row"}` means the cloud and
  Postgres had drifted apart — expected occasionally, but a sustained rate is worth
  investigating because orphans bill until they are swept.

The `reason` labels on `duckhaven_agents_reaped_total` are the same strings the per-agent
monitoring page records against each lifecycle transition, so an alert and the UI always agree
about why an agent went away.

### Blocked sandbox escapes

A [SQL session](../concepts/sql-sessions.md) statement that tries to leave its sandbox is
rejected at one of two layers, and each is observable:

- **At the API** — `duckhaven_statement_policy_rejections_total{rule}` counts statements the
  capability-scoped policy refused, broken down by rule (`read_path`, `copy_path`,
  `attach_target`, `set_name`, `install`, `command`, `unparseable`, …). A steady trickle is
  normal for an exploratory user; a sustained rate on `read_path`/`copy_path` from one
  principal is worth looking at.
- **At the agent** — a statement blocked by DuckDB's own guards (a disabled filesystem, or a
  `SET` refused because the configuration is locked) is logged at `WARNING` as
  `Statement blocked by the DuckDB sandbox: …` and also lands in
  `duckhaven_sql_statements_total{status="failed"}`. Reaching this layer means the statement
  got past the API policy, so a recurring one is worth investigating rather than tuning away.

Note that the **network egress** restriction has no metric of its own: a blocked connection
surfaces as an ordinary statement failure with a connection error. It is verified by the
runtime check in [Sandboxing](../concepts/sql-sessions.md#sandboxing), not by a counter.

### Behavior under high availability

Under the opt-in [HA topology](../deployment/high-availability.md) several API replicas run at
once. The metrics are designed so a `sum` across replicas is always correct:

- **Query and HTTP counters/histograms** are per-replica; each event is handled by one replica.
- **Agent gauges** come only from the sockets a replica currently owns (its in-memory ring
  buffer), so a connected agent appears under exactly one replica's scrape. `agent_id` is the
  dedup key. The control plane never dials an agent to gather these — it reads samples the
  agent already pushed over its control connection.
- **Maintenance-scanner gauges** are emitted only by the replica that currently holds the
  scanner's Postgres advisory lock (the same leader election that runs the scan), so they form
  a single cluster-wide series. They may briefly disappear for one scan tick after a leader
  failover.

### Cardinality policy

Cardinality is the main operational risk, so labels are deliberately bounded:

- **Permitted:** `replica_id`, `agent_id` + `agent_name`, `status`, `method`, `route`
  (the matched route *template*, never the raw URL), `severity`, `profile`.
- **Never used as labels:** workspace id, user id, catalog/schema/table names, raw URL paths,
  SQL text, query id, agent host/IP — any of these would grow unbounded on a long-lived
  deployment.

### Starter alert rules

DuckHaven has no alert engine of its own; alert on these from Prometheus. They alert on **symptoms** — users
waiting, queries failing, agents running out of memory — rather than on causes such as high CPU, which the
monitoring page is for. Tune the thresholds to your workload.

```yaml
groups:
  - name: duckhaven
    rules:
      - alert: DuckHavenQueriesWaiting
        # Interactive queries only: the queue-wait histogram is recorded when a worksheet or API query starts running.
        expr: histogram_quantile(0.95, sum by (le) (rate(duckhaven_query_queue_wait_seconds_bucket[10m]))) > 10
        for: 10m
        annotations:
          summary: "p95 wait before queries start running is above 10s: add an agent or a larger one"
      - alert: DuckHavenPlatformFailures
        # Leaves out queries that failed on their own SQL, and cancellations.
        expr: |
          sum(rate(duckhaven_query_failures_total{reason!="sql_error"}[15m]))
            / sum(rate(duckhaven_queries_total[15m])) > 0.05
        for: 15m
        annotations:
          summary: "More than 5% of queries are failing for reasons other than their own SQL"
      - alert: DuckHavenAgentOOMKill
        expr: increase(duckhaven_agent_oom_kills_total[15m]) > 0
        annotations:
          summary: "The kernel killed a process for memory on {{ $labels.agent_name }}"
      - alert: DuckHavenEstimatesAbandoned
        # A gauge that only rises until the agent restarts; each one costs a worker thread.
        expr: delta(duckhaven_agent_estimates_abandoned[1h]) > 0
        annotations:
          summary: "{{ $labels.agent_name }} abandoned a query estimate; consider restarting it"
      - alert: DuckHavenProvisioningFailing
        expr: increase(duckhaven_agent_provisions_total{outcome="failure"}[30m]) > 0
        annotations:
          summary: "Elastic compute failed to start: queries waiting for it will not run"
```

### Starter Grafana dashboard

Import this minimal dashboard (Grafana → Dashboards → New → Import) and point it at your
Prometheus data source. It covers query throughput, failure rate, latency, agent saturation,
out-of-memory kills, and open maintenance recommendations — extend it from there.

```json
{
  "title": "DuckHaven",
  "schemaVersion": 39,
  "timezone": "browser",
  "panels": [
    {
      "type": "timeseries",
      "title": "Query rate by status",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "targets": [
        { "expr": "sum by (status) (rate(duckhaven_queries_total[5m]))", "legendFormat": "{{status}}" }
      ]
    },
    {
      "type": "stat",
      "title": "Query failure ratio (5m)",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "targets": [
        {
          "expr": "sum(rate(duckhaven_queries_total{status=\"failed\"}[5m])) / sum(rate(duckhaven_queries_total[5m]))"
        }
      ]
    },
    {
      "type": "timeseries",
      "title": "Query duration p95",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "targets": [
        {
          "expr": "histogram_quantile(0.95, sum by (le) (rate(duckhaven_query_duration_seconds_bucket[5m])))",
          "legendFormat": "p95"
        }
      ]
    },
    {
      "type": "timeseries",
      "title": "Agent CPU %",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 8 },
      "targets": [
        { "expr": "duckhaven_agent_cpu_percent", "legendFormat": "{{agent_name}}" }
      ]
    },
    {
      "type": "timeseries",
      "title": "Queue depth by agent",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 16 },
      "targets": [
        { "expr": "duckhaven_agent_queued_queries", "legendFormat": "{{agent_name}}" }
      ]
    },
    {
      "type": "timeseries",
      "title": "Queries executing by agent",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 24 },
      "targets": [
        { "expr": "duckhaven_agent_executing_queries", "legendFormat": "{{agent_name}}" }
      ]
    },
    {
      "type": "stat",
      "title": "OOM kills (24h)",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 24 },
      "targets": [
        { "expr": "sum(increase(duckhaven_agent_oom_kills_total[24h]))" }
      ]
    },
    {
      "type": "stat",
      "title": "Open maintenance recommendations",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 16 },
      "targets": [
        { "expr": "sum(duckhaven_maintenance_open_recommendations)" }
      ]
    }
  ]
}
```

## Related

- [Distributed tracing](tracing.md) — per-request OpenTelemetry traces, the other half of observability.
- [Query execution](../concepts/query-execution.md) — what the counters reflect.
- [Operator runbook](runbook.md) — procedures for running the cluster.
- [Configuration reference](../reference/configuration.md#observability) — the `METRICS_ENABLED` knob.
