# SQL support

DuckHaven validates every statement against an allowlist at the API boundary (parse-only — the control plane never
executes user SQL) before dispatching it to an [agent](../concepts/agents.md).

## Allowed statements

| Category | Statements |
|---|---|
| Data | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE` |
| Catalog DDL | `CREATE`, `ALTER`, `DROP` (schemas and tables) |

These run on the agent against the workspace's attached Polaris REST catalogs. A single `SELECT` is materialized to
Parquet and returned as a result grid; other statements run and report status without a grid.

### Addressing catalogs

Every catalog attached to the workspace is available in a query. Unqualified names (`schema.table`) resolve against the
worksheet's **active catalog**; reference another catalog — or join across catalogs — with a fully-qualified
`catalog.schema.table`:

```sql
SELECT * FROM raw.analytics.events e
JOIN curated.analytics.users u ON e.user_id = u.id;
```

## Inspecting metadata (`information_schema`)

Every catalog exposes a built-in, **read-only** `information_schema` — present by default, the way Snowflake gives every
database an `INFORMATION_SCHEMA` and Databricks gives every catalog an `information_schema`. You never create it and you
cannot write to it; it simply describes the objects in the catalogs attached to your worksheet. It is **always
present**, not the working schema — bare `schema.table` names still resolve against your catalog's data (default
`analytics`), exactly as before.

It is DuckDB's native, live `information_schema`: a single view set that spans **all** attached catalogs and is computed
per query (never cached). Scope it to one catalog with `table_catalog`:

```sql
-- Tables and views in a catalog
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_catalog = 'analytics';

-- Namespaces (schemas) in a catalog
SELECT schema_name
FROM information_schema.schemata
WHERE catalog_name = 'analytics';
```

For a table's columns and types, use `DESCRIBE` (which DuckHaven treats as a read-only query):

```sql
DESCRIBE analytics.analytics.events;
SELECT column_name, column_type FROM (DESCRIBE analytics.analytics.events);
```

!!! note "`information_schema.columns` and Iceberg"
    DuckDB's `information_schema.columns` (and `duckdb_columns()`) cannot yet introspect the columns of an attached
    Iceberg REST table — they return a placeholder rather than the real columns. Use `DESCRIBE` (above) or the
    [catalog browser](../guides/run-queries.md), which reads columns directly from Polaris. Constraint/key views
    (`table_constraints`, `key_column_usage`) are intentionally absent: Iceberg has no enforced primary or foreign keys.

For Iceberg-native facts — snapshot history, file and manifest details — use the `iceberg` table functions:

```sql
SELECT * FROM iceberg_snapshots('analytics.analytics.events');
SELECT * FROM iceberg_metadata('analytics.analytics.events');
```

See [Snapshots & time travel](../guides/snapshots-time-travel.md) for more on snapshot history.

## Rejected statements

Anything that could break out of the per-query sandbox is rejected, including:

`ATTACH` / `DETACH`, `COPY` / `EXPORT`, `INSTALL` / `LOAD`, `SET` / `PRAGMA`, `CALL`, `VACUUM`, and transaction control.

## Time-travel syntax

Query an Iceberg table as of a past snapshot using DuckDB's `AT` clause (see
[Snapshots & time travel](../guides/snapshots-time-travel.md)):

```sql
SELECT * FROM analytics.events AT (VERSION => 7287998166701990000);
SELECT * FROM analytics.events AT (TIMESTAMP => '2026-05-01 00:00:00');
```

## Concurrency control command

A worksheet can change an agent's [concurrency profile](../concepts/query-execution.md) with a DuckHaven control
command (its own statement):

```sql
SET duckhaven_concurrency = 'auto';   -- auto | single | equal_2 | decaying_2 | decaying_3
RESET duckhaven_concurrency;          -- back to the default
```

It applies to the selected agent and is agent-global. See
[Runbook §6](../operations/runbook.md#6-query-queueing-concurrency).
