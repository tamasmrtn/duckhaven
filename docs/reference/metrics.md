---
type: reference
---

# Metrics

Every Prometheus metric the API serves at `GET /api/metrics`.
[Monitoring](../operations/monitoring.md#prometheus-metrics) covers scraping them, alert rules, and a
starter Grafana dashboard.

Counters and histograms are per-replica (carry a `replica_id` label). Aggregate them with
`sum`/`rate` across replicas — every underlying event (a query completing, a request being
served) happens on exactly one replica, so summing never double-counts. Counter series carry
the conventional `_total` suffix in the exposition (for example `duckhaven_queries_total`).

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `duckhaven_queries_submitted_total` | counter | `replica_id` | User queries accepted for dispatch (excludes internal/maintenance queries). |
| `duckhaven_queries_total` | counter | `replica_id`, `status` | User queries reaching a terminal state (`done`/`failed`/`cancelled`). |
| `duckhaven_query_failures_total` | counter | `replica_id`, `reason` | Failed user queries by classified cause. `sql_error` is a mistake in the query itself (parser, binder, catalog, conversion errors); the other reasons (`queue_full`, `out_of_memory`, `no_compute`, `timeout`, …) are the platform's. |
| `duckhaven_query_duration_seconds` | histogram | `replica_id` | Duration of completed (`done`) user queries. |
| `duckhaven_query_result_bytes` | histogram | `replica_id` | Result size of completed (`done`) user queries. |
| `duckhaven_query_queue_wait_seconds` | histogram | `replica_id` | Time a user query waited in the agent admission queue before running. |
| `duckhaven_query_queue_rejected_total` | counter | `replica_id`, `reason` | User queries rejected by agent admission control (`reason`: `queue_full`/`queued_timeout`). |
| `duckhaven_result_cache_lookups_total` | counter | `replica_id`, `outcome`, `reason` | [Result cache](../querying/result-cache.md) lookups by `outcome` and the run's `cache_detail`; hits are not in `duckhaven_queries_total`. |
| `duckhaven_result_cache_lookup_seconds` | histogram | `replica_id` | Time to decide whether a query is a hit. |
| `duckhaven_result_cache_admissions_total` | counter | `replica_id`, `outcome`, `reason` | Finished runs `admitted` (`inline`/`agent`) or `refused` with a reason. |
| `duckhaven_result_cache_revalidations_total` | counter | `replica_id`, `catalog_kind` | Hits served after compaction-only commits. |
| `duckhaven_result_cache_evictions_total` | counter | `replica_id`, `reason` | Entries removed (`expired`/`stale`/`space`/`gone`/`replaced`). |
| `duckhaven_result_cache_bytes` | gauge | `replica_id`, `storage` | Result bytes held, `inline` or `agent`. |
| `duckhaven_dashboard_runs_total` | counter | `replica_id`, `outcome` | [Dashboard](../querying/dashboards.md) views by `outcome`: `done`, `cancelled` (the viewer left) or `failed`. |
| `duckhaven_dashboard_run_seconds` | histogram | `replica_id` | Time from a dashboard view's request to its last widget. |
| `duckhaven_dashboard_widgets_total` | counter | `replica_id`, `state`, `cache` | Widgets answered, by `state` (`ok`/`empty`/`error`/`no_access`) and `cache` outcome (`none` when no query ran). |
| `duckhaven_http_requests_total` | counter | `replica_id`, `method`, `route`, `status` | REST API requests, keyed by route template. |
| `duckhaven_http_request_duration_seconds` | histogram | `replica_id`, `method`, `route` | REST API request latency. |
| `duckhaven_polaris_requests_total` | counter | `replica_id`, `operation`, `status` | Requests to Apache Polaris (Iceberg REST + management). `status` is the HTTP code, or `error` for transport failures. |
| `duckhaven_ducklake_queries_total` | counter | (same) | Metadata queries to a DuckLake catalog database — the counterpart to the Polaris rows above, so a slow browse appears in one or the other by kind. `status` is `ok`, `error`, or `empty` (no agent has attached it). |
| `duckhaven_ducklake_query_duration_seconds` | histogram | (same) | Latency of the above. |
| `duckhaven_polaris_request_duration_seconds` | histogram | `replica_id`, `operation` | Latency of requests to Apache Polaris. |
| `duckhaven_agent_up` | gauge | `replica_id`, `agent_id`, `agent_name` | `1` for each agent with a recent sample owned by this replica. |
| `duckhaven_agent_cpu_percent` | gauge | (same) | Agent CPU utilization. |
| `duckhaven_agent_memory_percent` | gauge | (same) | Agent memory utilization. |
| `duckhaven_agent_running_queries` | gauge | (same) | Admission slots in use on the agent, **including idle held SQL sessions**. For "queries actually running" use `duckhaven_agent_executing_queries`. |
| `duckhaven_agent_executing_queries` | gauge | (same) | Statements running on the agent; an idle held SQL session is not counted. Absent for agents too old to report it. |
| `duckhaven_agent_idle_sessions` | gauge | (same) | Open SQL sessions on the agent that are not running a statement. |
| `duckhaven_agent_memory_peak_percent` | gauge | (same) | Highest memory use reached during the agent's last 2-second sample interval, including spikes between samples. |
| `duckhaven_agent_cpu_seconds_total` | counter | (same) | CPU time consumed by the agent's cgroup. `rate()` of it is exact CPU use over any window, however short the bursts inside it. |
| `duckhaven_agent_oom_kills_total` | counter | (same) | Processes the kernel's OOM killer has killed in the agent's cgroup. As a counter, no scrape interval can miss one. |
| `duckhaven_agent_queued_queries` | gauge | (same) | Queries queued on the agent. |
| `duckhaven_agent_growth_waiting` | gauge | (same) | Statements parked waiting for memory to grow into — already admitted, unlike `queued_queries`. A steady non-zero value alongside near-zero `duckhaven_agent_cpu_percent` means statements are waiting on each other rather than on work, and is the signal to look at. |
| `duckhaven_agent_estimates_abandoned` | gauge | (same) | Query-cost estimates the agent gave up on because DuckDB's planner stopped responding. One whose planner never returns costs the agent a worker thread and a CPU core until it restarts (a merely slow one gives them back when it finishes), and the affected query is sized from a default rather than its real estimate — so this should stay flat. A rising value on one agent is a reason to restart it. |
| `duckhaven_agent_active_profile_info` | gauge | (same) + `profile` | Active concurrency profile (value always `1`). |
| `duckhaven_agents` | gauge | `provider`, `lifecycle` | Elastic agents by backend and lifecycle state. Reported by the reap leader only, so it is a cluster-wide count — do not sum it across replicas. |
| `duckhaven_agent_provisions_total` | counter | `replica_id`, `provider`, `runtime`, `outcome` | Elastic provisioning attempts (`outcome`: `success`/`failure`). |
| `duckhaven_agent_provisioning_seconds` | histogram | `replica_id`, `provider`, `runtime` | Time the backend took to create the instance, not the full cold start. Successes only. |
| `duckhaven_agents_reaped_total` | counter | `replica_id`, `reason` | Elastic agents torn down by the reaper (`reason`: `idle`/`max_lifetime`/`provisioning_timeout`/`orphan`/`dead_row`). |
| `duckhaven_query_compute_wait_seconds` | histogram | `replica_id`, `origin`, `outcome` | Wait for compute, cold start included ([labels](../compute/elastic-compute.md#watching-the-wait)). |
| `duckhaven_queries_parked`, `…_oldest_age_seconds` | gauge | `kind` | Queries waiting for an agent, and the longest wait (reap leader only). |
| `duckhaven_stranded_work_failed_total` | counter | `replica_id`, `kind` | Work failed for want of compute. |
| `duckhaven_compute_unavailable_total` | counter | `replica_id`, `reason` | Requests refused for want of compute. |
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
  each with its `_duration_seconds` companion, surface each catalog kind's error rate and latency; watch whichever
  kinds are deployed. Alert on a non-zero rate of `status="error"` (or 5xx) here to catch catalog-layer degradation
  before it manifests as mysterious query failures.
- **Elastic supply** — `duckhaven_agent_provisions_total{outcome="failure"}` and
  `duckhaven_agents_reaped_total{reason="provisioning_timeout"}` both mean users are waiting for
  compute that never arrives, which surfaces to them as a query that never starts.
  `duckhaven_agents_reaped_total{reason="orphan"}` or `{reason="dead_row"}` means the cloud and
  Postgres had drifted apart — expected occasionally, but a sustained rate is worth
  investigating because orphans bill until they are swept.

The `reason` labels on `duckhaven_agents_reaped_total` are the same strings the per-agent
monitoring page records against each lifecycle transition, so an alert and the UI always agree
about why an agent went away.

## Related

- [Monitoring](../operations/monitoring.md): scraping, alerts and dashboards.
- [Distributed tracing](../operations/tracing.md): following one query across components.
