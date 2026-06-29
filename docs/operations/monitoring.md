# Monitoring

DuckHaven surfaces live compute utilization from the admin console, and a full query audit trail from the
**History** page.

## Agent utilization

**Admin → Utilization** shows two counters at the top — **running queries** and **queued queries**, aggregated across
[agents](../concepts/agents.md) — plus each agent's active concurrency profile. Agents also report live CPU and memory
utilization on every heartbeat.

A persistently non-zero queued count means an agent is saturated. Either raise its slot count (if per-query memory still
suffices) or add another agent — see [Scaling compute](scaling.md).

## Query history and audit log

Every query a member runs is recorded on the **History** page (newest first), excluding internal queries such as
table-sample previews. Each member sees the history for the workspace they are in, with a row click opening the
query's profile.

Administrators get an extra **This workspace / All workspaces** toggle on the same page. Switching to **All
workspaces** turns History into the global audit log: every query across every workspace, with a Workspace column and
a filter by user. This is admin-only — a member can never read another workspace's queries. Each record captures the
SQL, status, row count, duration, result size, and any error. There is no separate audit table; the audit log is the
query record itself.

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
| `duckhaven_http_requests_total` | counter | `replica_id`, `method`, `route`, `status` | REST API requests, keyed by route template. |
| `duckhaven_http_request_duration_seconds` | histogram | `replica_id`, `method`, `route` | REST API request latency. |
| `duckhaven_agent_up` | gauge | `replica_id`, `agent_id`, `agent_name` | `1` for each agent with a recent sample owned by this replica. |
| `duckhaven_agent_cpu_percent` | gauge | (same) | Agent CPU utilization. |
| `duckhaven_agent_memory_percent` | gauge | (same) | Agent memory utilization. |
| `duckhaven_agent_running_queries` | gauge | (same) | Queries running on the agent. |
| `duckhaven_agent_queued_queries` | gauge | (same) | Queries queued on the agent. |
| `duckhaven_agent_active_profile_info` | gauge | (same) + `profile` | Active concurrency profile (value always `1`). |
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

- [Query execution](../concepts/query-execution.md) — what the counters reflect.
- [Operator runbook](runbook.md) — procedures for running the cluster.
- [Configuration reference](../reference/configuration.md#observability) — the `METRICS_ENABLED` knob.
