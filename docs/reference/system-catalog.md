---
type: reference
---

# System catalog tables

Every table and column of the `duckhaven` system catalog, which you query in SQL: `info_schema` as any member, the rest
with the permission each table needs. What the catalog is, and how a query reads it, is in
[System catalog](../querying/system-catalog.md).

| Table | Holds | Needs | Kept for |
|---|---|---|---|
| [`duckhaven.info_schema.catalogs`](#info_schemacatalogs) | The catalogs attached to the query's workspace | Nothing | Replaced after each access change, and at least every 5 minutes |
| [`duckhaven.info_schema.schemata`](#info_schemaschemata) | The schemas you can see | Nothing | Replaced as each catalog is read again |
| [`duckhaven.info_schema.tables`](#info_schematables) | The tables you can see | Nothing | Replaced as each catalog is read again |
| [`duckhaven.info_schema.columns`](#info_schemacolumns) | The columns of the tables you can see | Nothing | Replaced as each catalog is read again |
| [`duckhaven.access.audit`](#accessaudit) | Every event of the [access audit log](../access/audit-log.md) | `audit:read` | `AUDIT_RETENTION_DAYS`, as in the audit log |
| [`duckhaven.access.grants`](#accessgrants) | Current access, one row per grant of any kind | `audit:read` | Replaced whole after each access change, and at least every 5 minutes |
| [`duckhaven.access.users`](#accessusers) | Every user and service account | `audit:read` | Replaced with `access.grants` |
| [`duckhaven.access.workspaces`](#accessworkspaces) | Every workspace | `audit:read` | Replaced with `access.grants` |
| [`duckhaven.access.credentials`](#accesscredentials) | Every personal access token, never its secret | `audit:read` | Replaced with `access.grants` |
| [`duckhaven.access.table_access`](#accesstable_access) | Each table a finished query read or wrote | `audit:read` | As `query.history` |
| [`duckhaven.query.history`](#queryhistory) | Finished queries: `done`, `failed` or `cancelled` | `queries:admin` | `SYSTEM_CATALOG_QUERY_HISTORY_DAYS`, 365 days by default, counted from when each finished |
| [`duckhaven.query.sessions`](#querysessions) | [SQL sessions](../querying/sql-sessions.md), open and ended | `queries:admin` | `SYSTEM_CATALOG_RETENTION_DAYS`, 365 days by default, counted from when each opened |
| [`duckhaven.lineage.table_lineage`](#lineagetable_lineage) | Which tables feed which | `audit:read` | Replaced whole at least every 5 minutes |
| [`duckhaven.lineage.column_lineage`](#lineagecolumn_lineage) | Which columns feed which | `audit:read` | Replaced whole at least every 5 minutes |
| [`duckhaven.jobs.schedules`](#jobsschedules) | Every [schedule](../querying/schedule-queries.md), and how it last ran | `queries:admin` | Replaced whole every minute |
| [`duckhaven.compute.agents`](#computeagents) | Every agent, and its state | `agents:manage` | Replaced whole every minute |
| [`duckhaven.compute.agent_events`](#computeagent_events) | Each time an agent started, connected, disconnected or stopped | `agents:manage` | `SYSTEM_CATALOG_RETENTION_DAYS`, 365 days by default |
| [`duckhaven.compute.agent_utilization`](#computeagent_utilization) | Each agent's CPU, memory and queue, minute by minute | `agents:manage` | `SYSTEM_CATALOG_RETENTION_DAYS`, 365 days by default |
| [`duckhaven.storage.table_health`](#storagetable_health) | Each table's [health](../data/maintenance.md#health-score) at each maintenance scan | `maintenance:manage` | `SYSTEM_CATALOG_RETENTION_DAYS`, 365 days by default |
| [`duckhaven.storage.maintenance_operations`](#storagemaintenance_operations) | Each [maintenance apply](../data/maintenance.md#applying-maintenance), and how it ended | `maintenance:manage` | `SYSTEM_CATALOG_RETENTION_DAYS`, 365 days by default |

- **Columns are only added.** A column is never renamed, retyped or removed. Name the columns you read rather than
  `SELECT *` in anything that must keep working.
- **Names are copied with the ids.** Emails, workspace slugs, catalog slugs and agent names are kept as they were when
  the row was copied, so a row still reads after the user, workspace or agent is deleted.
- **`info_schema` rows are filtered.** Each query sees the rows of its own workspace's catalogs that everyone it runs
  for can see; see [What info_schema shows](../querying/system-catalog.md#what-info_schema-shows), which also says how
  soon a change appears.
- **The copy keeps what the source drops.** Agent events and minutes, table health samples and maintenance applies
  stay in the copy for their own retention, after DuckHaven's own pages have let them go, and after the agent, table or
  workspace is deleted.
- **JSON columns are text.** `before`, `after`, `details`, `tags`, `factors` and `result` hold JSON; read a field with
  `->>`, as in `after ->> 'tier'`.

## info_schema.catalogs

One row per catalog attached to the workspace the query runs in, whatever the grants on it.

| Column | Type | Meaning |
|---|---|---|
| `workspace_id` | `UUID` | The workspace |
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | The catalog, by the name queries use |
| `catalog_kind` | `VARCHAR` | `iceberg_polaris` or `ducklake`; see [Catalog kinds](../data/catalogs.md#catalog-kinds) |
| `access_mode` | `VARCHAR` | `open` or `scoped`, the [access mode](../access/scoped-access.md) of this attachment |
| `is_default` | `BOOLEAN` | Whether it is the workspace's default catalog |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When the catalog was created |
| `attached_at` | `TIMESTAMP WITH TIME ZONE` | When it was attached to the workspace |

## info_schema.schemata

One row per schema you can see.

| Column | Type | Meaning |
|---|---|---|
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its catalog |
| `schema_name` | `VARCHAR` | The schema |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was last read from the catalog |

## info_schema.tables

One row per table you can see. Views are not listed.

| Column | Type | Meaning |
|---|---|---|
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its catalog |
| `schema_name`, `table_name` | `VARCHAR` | Its schema and name |
| `table_type` | `VARCHAR` | `BASE TABLE` |
| `table_format` | `VARCHAR` | `ICEBERG` or `DUCKLAKE` |
| `column_count` | `INTEGER` | Its top-level columns |
| `row_count` | `BIGINT` | Its rows, as its page in the catalog tree shows them; empty when unknown |
| `bytes` | `BIGINT` | The size of its data files, as its page shows it; empty when unknown |
| `table_owner` | `VARCHAR` | The email of whoever created it on the **Catalog** page; empty for a table created any other way |
| `last_altered` | `TIMESTAMP WITH TIME ZONE` | Its last change: for an Iceberg table, by any engine; for a DuckLake table, the last DuckHaven saw after a write. Empty when unknown |
| `refreshed_at` | `TIMESTAMP WITH TIME ZONE` | When it was last read from the catalog |

## info_schema.columns

One row per top-level column of each table you can see. A nested column's fields are in its `data_type`.

| Column | Type | Meaning |
|---|---|---|
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | Its table's catalog |
| `schema_name`, `table_name` | `VARCHAR` | Its table |
| `column_name` | `VARCHAR` | The column |
| `ordinal_position` | `INTEGER` | Its place in the table, from 1 |
| `data_type` | `VARCHAR` | Its type as `DESCRIBE` shows it to a query, such as `BIGINT` or `STRUCT(x INTEGER, y VARCHAR)` |
| `source_type` | `VARCHAR` | Its type as the catalog stores it, such as Iceberg's `long` or DuckLake's `int64` |
| `is_nullable` | `VARCHAR` | `YES` or `NO` |

## access.audit

One row per audit event, with the fields of the [audit event](audit-events.md#event-fields) as columns. An event's
`label` is not copied.

| Column | Type | Event field |
|---|---|---|
| `id` | `UUID` | `id` |
| `event_time` | `TIMESTAMP WITH TIME ZONE` | `event_time` |
| `event_version` | `INTEGER` | `event_version` |
| `category` | `VARCHAR` | `category` |
| `action` | `VARCHAR` | `action` |
| `outcome` | `VARCHAR` | `outcome` |
| `actor_id` | `UUID` | `actor.id` |
| `actor_email` | `VARCHAR` | `actor.email` |
| `actor_type` | `VARCHAR` | `actor.type` |
| `on_behalf_of_id` | `UUID` | `actor.on_behalf_of_id` |
| `on_behalf_of_email` | `VARCHAR` | `actor.on_behalf_of_email` |
| `auth_method` | `VARCHAR` | `actor.auth_method` |
| `credential_id` | `UUID` | `actor.credential_id` |
| `request_id` | `VARCHAR` | `request.id` |
| `source_ip` | `VARCHAR` | `request.source_ip` |
| `user_agent` | `VARCHAR` | `request.user_agent` |
| `workspace_id` | `UUID` | `workspace.id` |
| `workspace_slug` | `VARCHAR` | `workspace.slug` |
| `target_type` | `VARCHAR` | `target.type` |
| `target_id` | `VARCHAR` | `target.id` |
| `target_name` | `VARCHAR` | `target.name` |
| `subject_id` | `UUID` | `subject.id` |
| `subject_email` | `VARCHAR` | `subject.email` |
| `self_change` | `BOOLEAN` | `self_change` |
| `before` | `VARCHAR`, JSON | `before` |
| `after` | `VARCHAR`, JSON | `after` |
| `details` | `VARCHAR`, JSON | `details` |
| `error_code` | `VARCHAR` | `error_code` |

## access.grants

One row per grant. `kind` says what the row grants, and so which columns are set; `snapshot_time` is set on every row.

| `kind` | Grants | `level` | Columns set |
|---|---|---|---|
| `global_role` | A [global role](permissions.md) | `admin` or `user` | `principal_*` |
| `workspace_member` | Membership of a workspace | `owner`, `writer` or `reader` | `principal_*`, `workspace_*` |
| `catalog_attachment` | A catalog to a workspace | `open` or `scoped`, the [access mode](../access/scoped-access.md) | `workspace_*`, `catalog_*`, `granted_by_*`, `granted_at` |
| `catalog_grant` | A [data grant](../access/scoped-access.md#grants-and-tiers) | `metadata`, `reader` or `writer` | `principal_*`, `catalog_*`, `schema_name`, `table_name`, `granted_by_*`, `granted_at` |
| `agent_access_mode` | An agent's [access mode](../access/agent-access.md#open-and-restricted-agents) | `open` or `restricted` | `agent_*` |
| `agent_grant` | An [agent grant](../access/agent-access.md) | `use`, `operate` or `admin` | `principal_*` or, for a grant to a workspace, `principal_type` and `workspace_*`; `agent_*`, `granted_by_*`, `granted_at` |
| `workspace_default_agent` | `use` of a workspace's default compute to its members | `use` | `workspace_*`, `agent_*` |

| Column | Type | Meaning |
|---|---|---|
| `kind` | `VARCHAR` | What the row grants; see above |
| `principal_type` | `VARCHAR` | `user`, `service_account`, or `workspace` for an agent grant to every member of one |
| `principal_id`, `principal_email` | `UUID`, `VARCHAR` | The account granted |
| `principal_active` | `BOOLEAN` | Whether that account can sign in; `false` once deactivated |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace |
| `catalog_id`, `catalog_slug` | `UUID`, `VARCHAR` | The catalog |
| `schema_name`, `table_name` | `VARCHAR` | What a `catalog_grant` covers. Both empty for the whole catalog, `table_name` empty for a whole schema |
| `agent_id`, `agent_name` | `UUID`, `VARCHAR` | The agent |
| `level` | `VARCHAR` | The role, mode or tier; see above |
| `granted_by_id`, `granted_by_email` | `UUID`, `VARCHAR` | Who made the grant, when recorded |
| `granted_at` | `TIMESTAMP WITH TIME ZONE` | When it was made, when recorded |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.users

One row per user and service account, deactivated ones included.

| Column | Type | Meaning |
|---|---|---|
| `user_id`, `email`, `name` | `UUID`, `VARCHAR`, `VARCHAR` | The account |
| `principal_type` | `VARCHAR` | `user` or `service_account` |
| `auth_provider` | `VARCHAR` | How it signs in: `local`, `ldap`, `oidc:⟨provider id⟩`, or `service_account` |
| `global_role` | `VARCHAR` | Its [global role](permissions.md): `admin` or `user` |
| `is_active` | `BOOLEAN` | Whether it can sign in; `false` once deactivated |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was created |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.workspaces

One row per workspace.

| Column | Type | Meaning |
|---|---|---|
| `workspace_id`, `workspace_slug`, `name` | `UUID`, `VARCHAR`, `VARCHAR` | The workspace |
| `description` | `VARCHAR` | Its description |
| `timezone` | `VARCHAR` | The time zone its schedules run in |
| `default_agent_id`, `default_agent_name` | `UUID`, `VARCHAR` | Its [default compute](../access/workspaces.md#default-compute); empty when it has none |
| `result_cache_enabled` | `BOOLEAN` | Whether the [result cache](../querying/result-cache.md) answers its queries |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was created |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.credentials

One row per [personal access token](rest-api.md#your-own-tokens), of a user or a service account. The token, and its
hash, are not copied. The [assistant](../ai/assistant.md)'s tokens, which last for one answer, are left out.

| Column | Type | Meaning |
|---|---|---|
| `credential_id` | `UUID` | The token's id, as in `credential_id` of `access.audit` |
| `kind` | `VARCHAR` | `pat` |
| `principal_type` | `VARCHAR` | `user` or `service_account` |
| `principal_id`, `principal_email` | `UUID`, `VARCHAR` | Who holds it |
| `principal_active` | `BOOLEAN` | Whether its holder can sign in |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was issued |
| `expires_at` | `TIMESTAMP WITH TIME ZONE` | When it stops working; empty for a token that never expires |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.table_access

One row per table, and kind of access, of each `done` query in `query.history`, worked out from its SQL. A query that
reads and writes the same table has two rows.

- **Names resolve as DuckDB binds them.** An unqualified name is in the catalog and schema the query ran in; a two-part
  `x.orders` is in catalog `x`'s `main` schema when `x` is a catalog, and otherwise in schema `x`.
- **Names are in lower case**, as DuckDB matches them.
- **Left out:** a table in no DuckHaven catalog (a temporary table, or this catalog), a `DESCRIBE`, and a query whose
  SQL DuckHaven cannot parse. A view counts as the view, not the tables it reads.

| Column | Type | Meaning |
|---|---|---|
| `query_id` | `UUID` | The query, as `id` in `query.history` |
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | The table's catalog |
| `schema_name`, `table_name` | `VARCHAR` | The table |
| `access` | `VARCHAR` | `read` or `write` |
| `finished_at` | `TIMESTAMP WITH TIME ZONE` | When the query finished |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace it ran in |
| `user_id`, `user_email` | `UUID`, `VARCHAR` | Who ran it |
| `on_behalf_of_id`, `on_behalf_of_email` | `UUID`, `VARCHAR` | The person a delegated run acted for |

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

## lineage.table_lineage

One row per relationship in the [lineage graph](../metadata/lineage.md), across every catalog, with every table named:
unlike the graph in a workspace, nothing is hidden for lack of a grant.

| Column | Type | Meaning |
|---|---|---|
| `edge_id` | `UUID` | The relationship |
| `provider` | `VARCHAR` | What asserted it: `execution` for SQL DuckHaven ran, or an importer such as `dbt` |
| `operation` | `VARCHAR` | The statement that produced it, such as `insert` or `create_table_as` |
| `confidence` | `VARCHAR` | `exact` |
| `column_lineage` | `VARCHAR` | Whether its column detail is known: `derived`, `unknown` or `unsupported`; see [Column-level lineage](../metadata/lineage.md#column-level-lineage) |
| `source_catalog_id`, `source_catalog_name` | `UUID`, `VARCHAR` | The source's catalog; empty for a source outside DuckHaven |
| `source_system` | `VARCHAR` | For a source outside DuckHaven, the system its producer named |
| `source_schema`, `source_table` | `VARCHAR` | The source |
| `target_catalog_id`, `target_catalog_name`, `target_system`, `target_schema`, `target_table` | as for the source | The target |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | Where it was observed |
| `last_query_id` | `UUID` | The query that last produced it |
| `first_seen_at`, `last_seen_at` | `TIMESTAMP WITH TIME ZONE` | When it was first and last observed |
| `observation_count` | `INTEGER` | How many times it has been observed |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## lineage.column_lineage

One row per column-to-column relationship, under a relationship of `table_lineage`.

| Column | Type | Meaning |
|---|---|---|
| `id` | `UUID` | The column relationship |
| `edge_id` | `UUID` | Its table relationship, as in `table_lineage` |
| `source_catalog_id`, `source_catalog_name`, `source_system`, `source_schema`, `source_table` | as in `table_lineage` | The source table |
| `source_column` | `VARCHAR` | The source column |
| `target_catalog_id`, `target_catalog_name`, `target_system`, `target_schema`, `target_table` | as in `table_lineage` | The target table |
| `target_column` | `VARCHAR` | The target column |
| `first_seen_at`, `last_seen_at` | `TIMESTAMP WITH TIME ZONE` | When it was first and last observed |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

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

One row per agent, static or [elastic](../compute/elastic-compute.md), as **Compute** lists it. A deleted agent drops
out; its events and minutes stay.

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

A metric the scan could not measure is empty.

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

## Related

- [System catalog](../querying/system-catalog.md): who can read it, and what a query may do with it.
- [Audit events](audit-events.md): every action, and what each records in `before`, `after` and `details`.
- [Enable the system catalog database](../deployment/system-catalog.md): turning it on, and how far behind it runs.
