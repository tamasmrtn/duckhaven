# Monitoring

DuckHaven gives every [agent](../concepts/agents.md) its own monitoring page, and records a full query audit trail on
the **History** page.

## Per-agent monitoring

Open **Compute** and click an agent to reach its detail page. The **Monitoring** tab answers the question an
operator actually has when something felt slow or a bill looked wrong: *what was this agent doing, and when?*

At the top, a row of **live statistics** — status, running queries, queued queries, and the agent's size — reads from
the agent's own 2-second sampler, so it reflects the present moment rather than a rolled-up average.

Below that, a **time range** control (1, 3, 8, 12, or 24 hours) governs every chart *and* the query list at the bottom
of the page together. The bucket size adapts to the range, from one minute at 1 hour to ten minutes at 24, so each
chart carries a comparable amount of detail whichever range you pick. The charts share a single time grid, which is
what makes them readable as a stack: a spike in one lines up with the same instant in all the others.

### The charts

**Peak query count** shows the highest number of concurrent queries the agent reported in each bucket, split into
*running* and *queued*. A persistently non-zero queued band means the agent is saturated: raise its slot count if
per-query memory still allows, or add another agent — see [Scaling compute](scaling.md). The depths come from the
agent's own admission queue, which is the only place that number exists; a query waiting in that queue is invisible to
the control plane's own timestamps.

**Completed query count** shows throughput as queries per minute, counting failed and cancelled runs alongside
successful ones — a query that stopped running is a query that finished occupying a slot.

**Agent activity** is the running/not-running timeline, banded by what the agent was doing:

- **Query activity** — queries were running or queued.
- **Other activity** — up, with no queries, but holding [SQL sessions](../concepts/sql-sessions.md) or fetching
  results. This is the band that explains an agent that stays alive while apparently doing nothing.
- **Ready** — up and idle. This is the time an idle timeout reclaims.
- **Starting** — an elastic agent provisioning, not yet accepting work.
- **Not running** — no agent.
- **No data** — no lifecycle history covers this period. Deliberately distinct from *Not running*: an agent that
  predates the lifecycle trail has no record, which is not the same as being known to have been off.

Above the chart, a summary reads *"Up 6h 12m · 41% busy · idle timeout 20 min"*. Those three numbers are only
meaningful together — a low busy share against a generous idle timeout is the clearest signal that an
[elastic agent](../concepts/elastic-compute.md) is being paid for while idle.

**Failures & rejections** breaks failed and cancelled runs down by cause — `queue_full`, `queued_timeout`,
`out_of_memory`, `no_compute`, `dispatch_failed`, `timeout` — rather than reporting one undifferentiated failure count.
The distinction matters because the fixes differ: queue rejections mean saturation, `no_compute` means elastic
provisioning never produced an agent, and an out-of-memory failure means the query needs a bigger agent. The chart is
hidden when nothing failed.

**Utilization** plots CPU and memory across the window. Buckets the agent reported nothing in are drawn as gaps rather
than zeros, so an outage never looks like an idle period.

Finally, the **History** list shows the runs that happened on this agent inside the selected window. Hovering a
duration splits it into queue wait and execution time — the difference between a slow query and a busy agent.

### Where the data comes from

Agents sample themselves every ~2 seconds, and those samples feed a short in-memory buffer for the live statistics.
For the historical windows they are additionally rolled up to **one row per agent per minute** and stored in Postgres,
alongside an append-only trail of agent lifecycle transitions. Both are retained for
`AGENT_METRICS_RETENTION_HOURS` (default one week) — comfortably longer than the 24 hours the UI offers, so widening
the range later does not require having planned for it.

This is deliberately DuckHaven's own storage rather than the [Prometheus](#prometheus-metrics) or
[tracing](tracing.md) pipelines below. Both of those are export-only and off by default; a built-in product page that
renders blank unless you deployed a collector would be the wrong default. The *instrumentation* is shared, though —
every durable row is written at the same point in the code that already emits the corresponding counter or span, and
reuses its vocabulary, so a Grafana alert and this page can never disagree about why an agent went away.

The query counts and failure breakdown are computed from the query records themselves, so they are exact rather than
sampled, and they exclude the same internal queries the History page does.

!!! note "Growth"
    The rollup and lifecycle tables are bounded by the retention setting. The `queries` table that backs the query
    charts and the audit log is **not** currently pruned — it grows for the life of the deployment.

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

The endpoint is **unauthenticated**, exactly like `/healthz` and `/readyz`: Prometheus
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
| `duckhaven_query_duration_seconds` | histogram | `replica_id` | Duration of completed (`done`) user queries. |
| `duckhaven_query_result_bytes` | histogram | `replica_id` | Result size of completed (`done`) user queries. |
| `duckhaven_query_queue_wait_seconds` | histogram | `replica_id` | Time a user query waited in the agent admission queue before running. |
| `duckhaven_query_queue_rejected_total` | counter | `replica_id`, `reason` | User queries rejected by agent admission control (`reason`: `queue_full`/`queued_timeout`). |
| `duckhaven_http_requests_total` | counter | `replica_id`, `method`, `route`, `status` | REST API requests, keyed by route template. |
| `duckhaven_http_request_duration_seconds` | histogram | `replica_id`, `method`, `route` | REST API request latency. |
| `duckhaven_polaris_requests_total` | counter | `replica_id`, `operation`, `status` | Requests to Apache Polaris (Iceberg REST + management). `status` is the HTTP code, or `error` for transport failures. |
| `duckhaven_polaris_request_duration_seconds` | histogram | `replica_id`, `operation` | Latency of requests to Apache Polaris. |
| `duckhaven_agent_up` | gauge | `replica_id`, `agent_id`, `agent_name` | `1` for each agent with a recent sample owned by this replica. |
| `duckhaven_agent_cpu_percent` | gauge | (same) | Agent CPU utilization. |
| `duckhaven_agent_memory_percent` | gauge | (same) | Agent memory utilization. |
| `duckhaven_agent_running_queries` | gauge | (same) | Queries running on the agent. |
| `duckhaven_agent_queued_queries` | gauge | (same) | Queries queued on the agent. |
| `duckhaven_agent_growth_waiting` | gauge | (same) | Statements parked waiting for memory to grow into — already admitted, unlike `queued_queries`. A steady non-zero value alongside near-zero `duckhaven_agent_cpu_percent` means statements are waiting on each other rather than on work, and is the signal to look at. |
| `duckhaven_agent_estimates_abandoned` | gauge | (same) | Query-cost estimates the agent gave up on because DuckDB's planner stopped responding. Each one costs the agent a worker thread and a CPU core until it restarts, and the affected query is sized from a default rather than its real estimate — so this should stay flat. A rising value on one agent is a reason to restart it. |
| `duckhaven_agent_active_profile_info` | gauge | (same) + `profile` | Active concurrency profile (value always `1`). |
| `duckhaven_agents` | gauge | `provider`, `lifecycle` | Elastic agents by backend and lifecycle state. Reported by the reap leader only, so it is a cluster-wide count — do not sum it across replicas. |
| `duckhaven_agent_provisions_total` | counter | `replica_id`, `provider`, `outcome` | Elastic provisioning attempts (`outcome`: `success`/`failure`). |
| `duckhaven_agent_provisioning_seconds` | histogram | `replica_id`, `provider` | Time to provision an elastic agent. Successes only — a failure's duration measures how long the backend took to say no, which would distort the cold-start percentiles. |
| `duckhaven_agents_reaped_total` | counter | `replica_id`, `reason` | Elastic agents torn down by the reaper (`reason`: `idle`/`max_lifetime`/`provisioning_timeout`/`orphan`/`dead_row`). |
| `duckhaven_db_pool_size` | gauge | `replica_id` | Configured connection-pool size. |
| `duckhaven_db_pool_checked_out` | gauge | `replica_id` | Connections currently checked out. |
| `duckhaven_db_pool_overflow` | gauge | `replica_id` | Connections beyond the configured pool size. |
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
- **Polaris dependency health** — `duckhaven_polaris_requests_total` / `_request_duration_seconds`
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

### Starter Grafana dashboard

Import this minimal dashboard (Grafana → Dashboards → New → Import) and point it at your
Prometheus data source. It covers query throughput, failure rate, latency, agent saturation,
and open maintenance recommendations — extend it from there.

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
