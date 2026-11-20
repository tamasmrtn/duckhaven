---
type: reference
---

# System catalog tables

Every table and column of the `duckhaven` system catalog, which you query in SQL with the permission each table
needs. What the catalog is, and how a query reads it, is in [System catalog](../querying/system-catalog.md).

| Table | Holds | Needs | Kept for |
|---|---|---|---|
| [`duckhaven.access.audit`](#accessaudit) | Every event of the [access audit log](../access/audit-log.md) | `audit:read` | `AUDIT_RETENTION_DAYS`, as in the audit log |
| [`duckhaven.access.grants`](#accessgrants) | Current access, one row per grant of any kind | `audit:read` | Replaced whole after each access change, and at least every 5 minutes |
| [`duckhaven.query.history`](#queryhistory) | Finished queries: `done`, `failed` or `cancelled` | `queries:admin` | `SYSTEM_CATALOG_QUERY_HISTORY_DAYS`, 365 days by default, counted from when each finished |

- **Columns are only added.** A column is never renamed, retyped or removed. Name the columns you read rather than
  `SELECT *` in anything that must keep working.
- **Names are copied with the ids.** Emails, workspace slugs, catalog slugs and agent names are kept as they were when
  the row was copied, so a row still reads after the user, workspace or agent is deleted.
- **JSON columns are text.** `before`, `after`, `details` and `tags` hold JSON; read a field with `->>`, as in
  `after ->> 'tier'`.

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

## Related

- [System catalog](../querying/system-catalog.md): who can read it, and what a query may do with it.
- [Audit events](audit-events.md): every action, and what each records in `before`, `after` and `details`.
- [Enable the system catalog database](../deployment/system-catalog.md): turning it on, and how far behind it runs.
