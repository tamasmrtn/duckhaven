---
type: reference
---

# System catalog tables

Every table of the `duckhaven` system catalog: what it holds, the permission it needs, how often it is refreshed and how
long it keeps its rows. Their columns are on three pages: [info_schema tables](system-catalog-info-schema.md), which
any member can read, [Access and lineage tables](system-catalog-access.md) and
[Operations tables](system-catalog-operations.md). What the catalog is, and how a query reads it, is in
[System catalog](../querying/system-catalog.md).

| Table | Holds | Needs | Refreshed | Kept for |
|---|---|---|---|---|
| [`duckhaven.info_schema.catalogs`](system-catalog-info-schema.md#info_schemacatalogs) | The catalogs attached to the query's workspace, with their kind and access mode | Nothing | After each access change, and every 5 minutes | Current rows |
| [`duckhaven.info_schema.schemata`](system-catalog-info-schema.md#info_schemaschemata) | The schemas you can see | Nothing | As its catalog is read | Current rows |
| [`duckhaven.info_schema.tables`](system-catalog-info-schema.md#info_schematables) | The tables you can see, with their format, row count, size, owner and last write | Nothing | As its catalog is read | Current rows |
| [`duckhaven.info_schema.columns`](system-catalog-info-schema.md#info_schemacolumns) | The columns of those tables, with the type a query sees | Nothing | As its catalog is read | Current rows |
| [`duckhaven.info_schema.routines`](system-catalog-info-schema.md#info_schemaroutines) | The macros of the DuckLake schemas you can see | Nothing | As its catalog is read | Current rows |
| [`duckhaven.info_schema.semantic_models`](system-catalog-info-schema.md#info_schemasemantic_models) | The workspace's [semantic models](../metadata/semantic-layer.md) whose tables you can see | Nothing | Every minute | Current rows |
| [`duckhaven.info_schema.semantic_datasets`](system-catalog-info-schema.md#info_schemasemantic_datasets) | The datasets of those models | Nothing | Every minute | Current rows |
| [`duckhaven.info_schema.semantic_dimensions`](system-catalog-info-schema.md#info_schemasemantic_dimensions) | Their dimensions | Nothing | Every minute | Current rows |
| [`duckhaven.info_schema.semantic_metrics`](system-catalog-info-schema.md#info_schemasemantic_metrics) | Their metrics | Nothing | Every minute | Current rows |
| [`duckhaven.info_schema.semantic_relationships`](system-catalog-info-schema.md#info_schemasemantic_relationships) | Their relationships | Nothing | Every minute | Current rows |
| [`duckhaven.access.audit`](system-catalog-access.md#accessaudit) | Every event of the [access audit log](../access/audit-log.md) | `audit:read` | Every 10 seconds | 365 days |
| [`duckhaven.access.grants`](system-catalog-access.md#accessgrants) | Current access, one row per grant of any kind | `audit:read` | After each access change, and every 5 minutes | Current rows |
| [`duckhaven.access.users`](system-catalog-access.md#accessusers) | Every user and service account, with its global role | `audit:read` | With `access.grants` | Current rows |
| [`duckhaven.access.workspaces`](system-catalog-access.md#accessworkspaces) | Every workspace | `audit:read` | With `access.grants` | Current rows |
| [`duckhaven.access.credentials`](system-catalog-access.md#accesscredentials) | Every personal access token, never its secret | `audit:read` | With `access.grants` | Current rows |
| [`duckhaven.access.table_access`](system-catalog-access.md#accesstable_access) | Each table a finished query read or wrote, and who ran it | `audit:read` | With `query.history` | As `query.history` |
| [`duckhaven.lineage.table_lineage`](system-catalog-access.md#lineagetable_lineage) | Which tables feed which, in every catalog | `audit:read` | Every 5 minutes | Current rows |
| [`duckhaven.lineage.column_lineage`](system-catalog-access.md#lineagecolumn_lineage) | Which columns feed which | `audit:read` | Every 5 minutes | Current rows |
| [`duckhaven.query.history`](system-catalog-operations.md#queryhistory) | Finished queries: `done`, `failed` or `cancelled` | `queries:admin` | Every 10 seconds | 365 days from finishing |
| [`duckhaven.query.sessions`](system-catalog-operations.md#querysessions) | [SQL sessions](../querying/sql-sessions.md), open and ended, and why each ended | `queries:admin` | Every 10 seconds | 365 days from opening |
| [`duckhaven.jobs.schedules`](system-catalog-operations.md#jobsschedules) | Every [schedule](../querying/schedule-queries.md), and how it last ran | `queries:admin` | Every minute | Current rows |
| [`duckhaven.compute.agents`](system-catalog-operations.md#computeagents) | Every agent, and its state | `agents:manage` | Every minute | Current rows |
| [`duckhaven.compute.agent_events`](system-catalog-operations.md#computeagent_events) | Each time an agent started, connected, disconnected or stopped | `agents:manage` | Every 10 seconds | 365 days |
| [`duckhaven.compute.agent_utilization`](system-catalog-operations.md#computeagent_utilization) | Each agent's CPU, memory and queue, minute by minute | `agents:manage` | Every 10 seconds | 365 days |
| [`duckhaven.storage.table_health`](system-catalog-operations.md#storagetable_health) | Each table's [health](../data/maintenance.md#health-score) at each maintenance scan | `maintenance:manage` | Every 10 seconds | 365 days |
| [`duckhaven.storage.maintenance_operations`](system-catalog-operations.md#storagemaintenance_operations) | Each [maintenance apply](../data/maintenance.md#applying-maintenance), and how it ended | `maintenance:manage` | Every 10 seconds | 365 days |
| [`duckhaven.storage.backends`](system-catalog-operations.md#storagebackends) | Every [storage backend](../data/storage-backends.md), and how many catalogs use it; never its configuration | `storage:manage` | Every minute | Current rows |
| [`duckhaven.ai.assistant_usage`](system-catalog-operations.md#aiassistant_usage) | The tokens each [assistant](../ai/assistant.md) answer used, and for whom | `queries:admin` | Every 10 seconds | 365 days |
| [`duckhaven.ai.tool_calls`](system-catalog-operations.md#aitool_calls) | Each tool the assistant called, and its status; never its arguments | `queries:admin` | Every 10 seconds | 365 days |

- **Refreshed.** The 10 seconds is `SYSTEM_CATALOG_PUBLISH_INTERVAL_S`. A table of current rows is replaced whole each
  time, so a deleted agent or schedule drops out of it. How soon a catalog's change reaches `info_schema` is in
  [What info_schema shows](../querying/system-catalog.md#what-info_schema-shows).
- **Kept for.** `AUDIT_RETENTION_DAYS` sets the period of `access.audit`, as of the audit log;
  `SYSTEM_CATALOG_QUERY_HISTORY_DAYS` that of `query.history` and `access.table_access`; and
  `SYSTEM_CATALOG_RETENTION_DAYS` that of every other table not of current rows. Each is 365 days by default, and `0`
  keeps every row; see [Configuration](configuration.md).

## Rules every table follows

- **Columns are only added.** A column is never renamed, retyped or removed. Name the columns you read rather than
  `SELECT *` in anything that must keep working.
- **Names are copied with the ids.** Emails, workspace slugs, catalog slugs and agent names are kept as they were when
  the row was copied, so a row still reads after the user, workspace or agent is deleted.
- **The copy keeps what the source drops.** Agent events and minutes, table health samples, maintenance applies,
  assistant answers and tool calls stay in the copy for their own retention. They stay after DuckHaven's own pages have
  let them go, and after the agent, table, conversation or workspace is deleted. Deleting a workspace removes its
  queries from History but not from the copy.
- **JSON columns are text.** `before`, `after`, `details`, `tags`, `factors`, `result`, `primary_key`, `synonyms`,
  `time_grains` and `join_columns` hold JSON; read a field with `->>`, as in `after ->> 'tier'`.
- **An empty value is unknown, or does not apply.** Where it means something more, such as a token that never expires,
  its column says so.

## Related

- [System catalog](../querying/system-catalog.md): who can read it, and what a query may do with it.
- [Audit events](audit-events.md): every action, and what each records in `before`, `after` and `details`.
- [Enable the system catalog database](../deployment/system-catalog.md): turning it on, and how far behind it runs.
