---
type: reference
---

# Monitoring

DuckHaven exports Prometheus metrics for alerting, records every query in History, and gives each agent a monitoring
page.

This page lists the metrics and how to read them. Each [agent](../compute/agents.md)'s own charts are described in
[Agent monitoring](../compute/agent-monitoring.md).

## Per-agent monitoring

Every agent has its own **Monitoring** tab (**Compute** → *an agent*): what it is doing at the moment, where its time
went, the queries it ran and what each cost, and its CPU and memory, over up to a week. See [Agent
monitoring](../compute/agent-monitoring.md) for what each chart shows and exactly how each number is measured.

## Query history and audit log

Every query, with the person or [service account](../access/service-accounts.md) who ran it, its SQL, status, rows,
duration, result size and any error, is recorded on the **History** page; there is no separate audit table. With
`queries:admin`, the **All workspaces** view makes it the audit log for every workspace. How to filter and read it is in
[Run queries → Find an earlier run](../querying/run-queries.md#find-an-earlier-run).

## Prometheus metrics

Each API replica exposes a Prometheus text-exposition endpoint at **`GET /api/metrics`**. It
re-exports the same data the admin console shows — live agent utilization, the query audit
log, and the maintenance scanner — plus standard HTTP, database-pool, and process telemetry,
so you can alert on saturation and failure rates instead of polling the REST API.

The endpoint is **unauthenticated**, exactly like `/api/healthz` and `/api/readyz`: Prometheus
scrapers carry no session cookie, and DuckHaven already assumes no public ingress. Keep it on
the internal network. Set `METRICS_ENABLED=false` (see the
[configuration reference](../reference/configuration.md#observability)) to remove it entirely.

The bundled Grafana is wired to Tempo for [traces](tracing.md) only; DuckHaven does **not**
ship a Prometheus. The scrape config and starter dashboard below, with the
[metric reference](../reference/metrics.md), are everything you need to wire the metrics into
your own Prometheus + Grafana.

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

Every metric and its labels are listed in the [metric reference](../reference/metrics.md).

### Blocked sandbox escapes

A [SQL session](../querying/sql-sessions.md) statement that tries to leave its sandbox is
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
runtime check in [Sandboxing](../querying/sql-sessions.md#sandboxing), not by a counter.

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
- [Query execution](../querying/query-execution.md) — what the counters reflect.
- [Troubleshoot the deployment](troubleshooting.md) — problems on the host and network.
- [Configuration reference](../reference/configuration.md#observability) — the `METRICS_ENABLED` knob.
