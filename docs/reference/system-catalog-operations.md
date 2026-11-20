---
type: reference
---

# Operations tables

Every column of the `duckhaven` tables of queries, SQL sessions, schedules, compute, storage and the assistant: the
`query`, `jobs`, `compute`, `storage` and `ai` schemas. The permission each needs, how often it is refreshed, how long
it keeps its rows, and the rules every table follows, are in [System catalog tables](system-catalog.md).

## query.history

One row per finished query, as History shows it. The queries DuckHaven runs for itself, such as table samples, metadata
reads and maintenance checks, are left out, and so are results and profiles.

| Column | Type | Meaning |
|---|---|---|
| `id` | `UUID` | The query's id, as in `GET /api/queries/{query_id}` |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace it ran in |
| `user_id`, `user_email` | `UUID`, `VARCHAR` | Who ran it |
| `on_behalf_of_id`, `on_behalf_of_email` | `UUID`, `VARCHAR` | The person a delegated run acted for, such as the person the [assistant](../ai/assistant.md) answered |
| `agent_id`, `agent_name` | `UUID`, `VARCHAR` | The agent that ran it |
| `runtime_id` | `VARCHAR` | The agent's [runtime](../compute/runtimes.md) |
| `origin` | `VARCHAR` | The kind of run: empty for a worksheet or API call, or one such as `scheduled`, `session` or `dashboard` |
| `statement_type` | `VARCHAR` | As in the [query history filters](rest-api.md#query-history): `select`, `insert`, …, `other`, or empty when it did not parse |
| `status` | `VARCHAR` | `done`, `failed` or `cancelled` |
| `error` | `VARCHAR` | Why it failed |
| `sql` | `VARCHAR` | The SQL as submitted |
| `row_count` | `BIGINT` | Rows in the result |
| `duration_ms` | `BIGINT` | The agent's execution time, when it reported one |
| `result_bytes` | `BIGINT` | The size of its stored result; empty for a statement with no result |
| `started_at` | `TIMESTAMP WITH TIME ZONE` | When it was submitted |
| `dispatched_at` | `TIMESTAMP WITH TIME ZONE` | When it was sent to an agent; empty for a SQL session statement |
| `running_at` | `TIMESTAMP WITH TIME ZONE` | When the agent started it; empty if it never started |
| `finished_at` | `TIMESTAMP WITH TIME ZONE` | When it finished |
| `session_id` | `UUID` | The [SQL session](../querying/sql-sessions.md) it ran in |
| `schedule_id` | `UUID` | The [schedule](../querying/schedule-queries.md) that ran it |
| `client_name`, `client_version` | `VARCHAR` | The tool that sent it, from its `User-Agent` |
| `tags` | `VARCHAR`, JSON | The query's tags |
| `active_catalog`, `active_schema` | `VARCHAR` | Where its unqualified names resolved; empty when it never ran |
| `cache_status` | `VARCHAR` | What the [result cache](../querying/result-cache.md) did: `hit`, `miss`, `bypass` or `ineligible`; empty when it did not look |
| `result_source_query_id` | `UUID` | For a `hit`, the query whose result it served |
| `cpu_time_ms` | `BIGINT` | DuckDB's CPU time, summed across threads, in whole milliseconds; see [Query profiles](../querying/query-profiles.md#waiting-versus-working) |
| `bytes_read` | `BIGINT` | Bytes it read |
| `peak_memory_bytes` | `BIGINT` | Its peak memory; in a SQL session, how far it raised the session's peak |
| `spill_bytes` | `BIGINT` | Bytes it spilled to disk; in a SQL session, how far it raised the session's total |

The last four are from the query's [profile](../querying/query-profiles.md), and are empty for a query without one,
such as most DDL.

## query.sessions

One row per [SQL session](../querying/sql-sessions.md), as the [session audit trail](../querying/session-audit.md)
shows it. A session appears within the publish interval of opening, and its row is updated until it ends. Its
statements are in `query.history`, with this `session_id`.

| Column | Type | Meaning |
|---|---|---|
| `session_id` | `UUID` | The session |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace it runs in |
| `user_id`, `user_email` | `UUID`, `VARCHAR` | Who opened it |
| `agent_id`, `agent_name` | `UUID`, `VARCHAR` | The agent holding it; empty while compute starts |
| `runtime_id` | `VARCHAR` | The agent's [runtime](../compute/runtimes.md) |
| `status` | `VARCHAR` | `pending`, `opening`, `open` or `closing` while it lives; `closed`, `expired` or `failed` once it ends |
| `close_reason` | `VARCHAR` | Why it ended: `client`, `idle`, `max_lifetime`, `open_timeout`, `agent_disconnect`, `agent_lease`, `failed`, `compute_timeout` or `provisioning_timeout`; see [Find out why a session ended](../querying/session-audit.md#find-out-why-a-session-ended) |
| `error` | `VARCHAR` | What went wrong, for a session that failed |
| `client_name`, `client_version` | `VARCHAR` | The tool that opened it, from its `User-Agent` |
| `active_catalog` | `VARCHAR` | The catalog it opened in |
| `use_cache` | `BOOLEAN` | Whether the [result cache](../querying/result-cache.md) may answer its statements |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was asked for |
| `opened_at` | `TIMESTAMP WITH TIME ZONE` | When it became usable; empty if it never did |
| `last_active_at` | `TIMESTAMP WITH TIME ZONE` | Its last statement |
| `closed_at` | `TIMESTAMP WITH TIME ZONE` | When it ended |

## jobs.schedules

One row per [schedule](../querying/schedule-queries.md), enabled or not.

| Column | Type | Meaning |
|---|---|---|
| `schedule_id` | `UUID` | The schedule, as `schedule_id` in `query.history` |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | Its workspace |
| `job_type` | `VARCHAR` | What it runs: `saved_query` |
| `saved_query_id`, `saved_query_name` | `UUID`, `VARCHAR` | The saved query it runs |
| `agent_id`, `agent_name` | `UUID`, `VARCHAR` | The agent it asks for; empty to let DuckHaven pick one |
| `cron` | `VARCHAR` | Its cron expression |
| `timezone` | `VARCHAR` | The workspace's time zone, which the cron expression is read in |
| `enabled` | `BOOLEAN` | Whether it runs |
| `next_run_at`, `last_run_at` | `TIMESTAMP WITH TIME ZONE` | Its next and last run |
| `last_run_query_id` | `UUID` | The query its last run started, as `id` in `query.history` |
| `last_run_status` | `VARCHAR` | That query's status: `queued` or `running` while it runs, then `done`, `failed` or `cancelled` |
| `created_by_id`, `created_by_email` | `UUID`, `VARCHAR` | Who created it |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was created |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## compute.agents

One row per agent, static or [elastic](../compute/elastic-compute.md), as **Compute** lists it.

| Column | Type | Meaning |
|---|---|---|
| `agent_id`, `name` | `UUID`, `VARCHAR` | The agent |
| `kind` | `VARCHAR` | `static` for one you run, `elastic` for one DuckHaven starts |
| `provider` | `VARCHAR` | For an elastic agent, the compute backend that starts it |
| `status` | `VARCHAR` | `healthy` while it is connected, otherwise `unavailable` |
| `lifecycle` | `VARCHAR` | For an elastic agent: `provisioning`, `running`, `terminating`, `terminated` or `failed` |
| `access_mode` | `VARCHAR` | `open` or `restricted`; see [Agent access](../access/agent-access.md) |
| `runtime_id` | `VARCHAR` | Its [runtime](../compute/runtimes.md), as it reported it, or as it was started before it has |
| `duckdb_version`, `agent_version` | `VARCHAR` | What it reported when it connected |
| `cores`, `memory_limit_gb` | `INTEGER`, `DOUBLE` | The cores and memory it reported |
| `requested_cpu`, `requested_memory_gb` | `DOUBLE` | The size an elastic agent was started at |
| `idle_timeout_s` | `DOUBLE` | Seconds idle before an elastic agent stops; empty for the default |
| `max_timeout_s` | `DOUBLE` | Its longest query timeout; empty for the image's default |
| `created_at`, `provisioned_at`, `terminated_at` | `TIMESTAMP WITH TIME ZONE` | When it was created, last started and last stopped |
| `last_ping_at` | `TIMESTAMP WITH TIME ZONE` | Its last heartbeat |
| `last_active_at` | `TIMESTAMP WITH TIME ZONE` | When it was last sent work |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## compute.agent_events

One row per change in an agent's state, as the [monitoring](../compute/agent-monitoring.md) timeline draws them.

| Column | Type | Meaning |
|---|---|---|
| `event_id` | `UUID` | The event |
| `agent_id`, `agent_name` | `UUID`, `VARCHAR` | The agent |
| `event` | `VARCHAR` | `provisioning`, `connected`, `disconnected`, `terminating`, `terminated` or `failed` |
| `reason` | `VARCHAR` | Why, when DuckHaven did it: `idle`, `max_lifetime`, `provisioning_timeout`, `orphan` or `dead_row` |
| `event_time` | `TIMESTAMP WITH TIME ZONE` | When it happened |

## compute.agent_utilization

One row per agent per minute it was connected, as the [monitoring](../compute/agent-monitoring.md) charts draw them.
A minute appears once it has ended.

| Column | Type | Meaning |
|---|---|---|
| `agent_id`, `agent_name` | `UUID`, `VARCHAR` | The agent |
| `minute` | `TIMESTAMP WITH TIME ZONE` | The minute's start |
| `cpu_avg_percent`, `cpu_max_percent` | `DOUBLE` | CPU in use, as a percentage of the agent's cores: the minute's average and highest sample |
| `memory_avg_percent`, `memory_max_percent` | `DOUBLE` | Memory in use, as a percentage of its limit; the highest includes peaks between samples |
| `running_queries_max`, `queued_queries_max` | `INTEGER` | The most queries running, and waiting in its queue, at once |
| `sessions_max` | `INTEGER` | The most SQL sessions it held at once |
| `sample_count` | `INTEGER` | Samples the minute is made of |
| `covered_seconds` | `DOUBLE` | Seconds of the minute the samples span |
| `oom_kills` | `INTEGER` | Processes the kernel killed for memory in the minute |

The last two are empty from an agent too old to report them.

## storage.table_health

One row per table per [maintenance scan](../data/maintenance.md#scanning) that read it. A scan skips a table whose
snapshot is unchanged, so a table has a row for each scan after it changed.

| Column | Type | Meaning |
|---|---|---|
| `sample_id` | `UUID` | The sample |
| `scanned_at` | `TIMESTAMP WITH TIME ZONE` | When the scan read the table |
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | The table's catalog |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace the scan ran in |
| `schema_name`, `table_name` | `VARCHAR` | The table |
| `score` | `INTEGER` | Its [health score](../data/maintenance.md#health-score), 0 to 100 |
| `snapshot_count`, `snapshot_id` | `INTEGER`, `BIGINT` | Its snapshots, and the current one |
| `oldest_snapshot_age_days` | `DOUBLE` | The age of its oldest snapshot |
| `data_file_count`, `manifest_count` | `INTEGER` | Its data files and manifests |
| `total_data_bytes`, `avg_file_bytes`, `metadata_bytes` | `BIGINT` | Its data, average file and metadata sizes |
| `orphan_bytes`, `orphan_file_count` | `BIGINT`, `INTEGER` | Files in its storage no snapshot references; empty for a scan that did not count them |
| `small_file_ratio` | `DOUBLE` | The share of its data files below the target size |
| `factors` | `VARCHAR`, JSON | Each dimension of the score, with its value and sub-score |

## storage.maintenance_operations

One row per [apply](../data/maintenance.md#applying-maintenance) of a maintenance recommendation. The row appears
within the publish interval of the apply starting and is updated when it ends.

| Column | Type | Meaning |
|---|---|---|
| `query_id` | `UUID` | The query that ran it, as `id` in `query.history` |
| `recommendation_id` | `UUID` | The recommendation applied |
| `kind` | `VARCHAR` | `compact_small_files`, `expire_snapshots`, `rewrite_manifests` or `cleanup_orphans` |
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | The table's catalog |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace it was applied from |
| `schema_name`, `table_name` | `VARCHAR` | The table; `expire_snapshots` and `cleanup_orphans` act on its whole catalog |
| `status` | `VARCHAR` | `running`, `succeeded` or `failed` |
| `error` | `VARCHAR` | Why it failed |
| `result` | `VARCHAR`, JSON | The table before and after, as the agent measured it, for a run that succeeded |
| `started_at`, `finished_at` | `TIMESTAMP WITH TIME ZONE` | When it started and ended |
| `applied_by_id`, `applied_by_email` | `UUID`, `VARCHAR` | Who applied it |

## storage.backends

One row per [storage backend](../data/storage-backends.md). Its configuration, the role ARN, external id or tenant id it
was registered with, is never copied.

| Column | Type | Meaning |
|---|---|---|
| `backend_id` | `UUID` | The backend |
| `name` | `VARCHAR` | Its name |
| `kind` | `VARCHAR` | `object_store`, `s3` or `adls_gen2` |
| `root_uri` | `VARCHAR` | Where its data lives |
| `catalog_count` | `INTEGER` | The catalogs on it |
| `created_by_id`, `created_by_email` | `UUID`, `VARCHAR` | Who registered it |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was registered |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When it was copied |

## ai.assistant_usage

One row per answer of the [assistant](../ai/assistant.md): what it cost, and for whom. What was asked and answered is
never copied.

| Column | Type | Meaning |
|---|---|---|
| `message_id` | `UUID` | The answer |
| `conversation_id` | `UUID` | Its conversation |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace it was asked in |
| `user_id`, `user_email` | `UUID`, `VARCHAR` | Who asked |
| `service_account_id`, `service_account_email` | `UUID`, `VARCHAR` | The service account the assistant acted as |
| `turn` | `INTEGER` | Its place in the conversation, from 0 |
| `input_tokens`, `output_tokens` | `INTEGER` | The tokens the model read and wrote for it |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was saved |

## ai.tool_calls

One row per tool the assistant called. Its arguments, the SQL it ran, what it returned and the tables it read are never
copied.

| Column | Type | Meaning |
|---|---|---|
| `call_id` | `UUID` | The call |
| `conversation_id` | `UUID` | Its conversation |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace |
| `user_id`, `user_email` | `UUID`, `VARCHAR` | Who the assistant was answering |
| `tool` | `VARCHAR` | The tool, such as `run_sql` |
| `status` | `VARCHAR` | `ok`; `denied`, refused on policy; `error`, failed for another reason; or `approval_required`, a write waiting for a person. See [How governance works](../ai/assistant.md#how-governance-works) |
| `query_id` | `UUID` | The query it ran, as `id` in `query.history`; empty for a tool that ran none |
| `latency_ms` | `INTEGER` | How long it took |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was saved |

## Related

- [System catalog tables](system-catalog.md): every table, the permission it needs, and how fresh it is.
- [Query profiles](../querying/query-profiles.md): what the cost columns of `query.history` measure.
- [Agent monitoring](../compute/agent-monitoring.md): the timeline and charts `compute` copies.
