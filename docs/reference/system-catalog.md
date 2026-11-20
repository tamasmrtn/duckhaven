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
| [`duckhaven.lineage.table_lineage`](#lineagetable_lineage) | Which tables feed which | `audit:read` | Replaced whole at least every 5 minutes |
| [`duckhaven.lineage.column_lineage`](#lineagecolumn_lineage) | Which columns feed which | `audit:read` | Replaced whole at least every 5 minutes |

- **Columns are only added.** A column is never renamed, retyped or removed. Name the columns you read rather than
  `SELECT *` in anything that must keep working.
- **Names are copied with the ids.** Emails, workspace slugs, catalog slugs and agent names are kept as they were when
  the row was copied, so a row still reads after the user, workspace or agent is deleted.
- **`info_schema` rows are filtered.** Each query sees the rows of its own workspace's catalogs that everyone it runs
  for can see; see [What info_schema shows](../querying/system-catalog.md#what-info_schema-shows), which also says how
  soon a change appears.
- **JSON columns are text.** `before`, `after`, `details` and `tags` hold JSON; read a field with `->>`, as in
  `after ->> 'tier'`.

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

## Related

- [System catalog](../querying/system-catalog.md): who can read it, and what a query may do with it.
- [Audit events](audit-events.md): every action, and what each records in `before`, `after` and `details`.
- [Enable the system catalog database](../deployment/system-catalog.md): turning it on, and how far behind it runs.
