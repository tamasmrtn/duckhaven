# SQL support

DuckHaven validates every statement against an allowlist at the API boundary (parse-only — the control plane never
executes user SQL) before dispatching it to an [agent](../concepts/agents.md).

## Allowed statements

| Category | Statements |
|---|---|
| Data | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `MERGE` |
| Catalog DDL | `CREATE`, `ALTER`, `DROP` (schemas and tables) |

These run on the agent against the workspace's attached Polaris REST catalogs. A single `SELECT` is materialized to
Parquet and returned as a result grid; other statements run and report status without a grid.

`TRUNCATE` is DuckDB's spelling of `DELETE FROM` without a `WHERE` clause — the two compile to exactly the same plan.
That is worth knowing on Iceberg, where it is **not** the cheap metadata-only operation the name suggests elsewhere:
emptying a table writes positional delete files proportional to its size, just as the equivalent `DELETE` would. To
discard a large table cheaply, drop and recreate it instead. Like any write, `TRUNCATE` requires `writer` access on its
target in a [scoped catalog](../concepts/permissions.md).

DuckDB also classifies its read-only introspection statements — `DESCRIBE`, `SHOW`, `SUMMARIZE`, and the `PRAGMA`s that
return rows (`PRAGMA version`, `PRAGMA table_info(…)`, `PRAGMA database_list`, `PRAGMA show_tables`) — as queries. They
are allowed on the same footing as a `SELECT` and return a result grid too. They read no files and change nothing. This
holds on both execution paths: a single query and a [SQL session](../concepts/query-execution.md) statement. `SUMMARIZE`
is the one that is not merely introspection — it scans the table to compute its statistics, so it needs read access to
the data, not just to the schema.

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

!!! warning "Not available in a workspace with a scoped catalog"
    Spanning every attachment is also why these views cannot be filtered by grant. They are rejected outright in any
    workspace that has at least one catalog attached in `scoped` mode — including from a worksheet whose active catalog
    is open, and even with a `table_catalog` filter. See
    [Discovering objects in a scoped catalog](../concepts/permissions.md#discovering-objects-in-a-scoped-catalog).

### Columns and types: use `DESCRIBE`

**`DESCRIBE` is the supported way to get a relation's columns and their types.** It is a read-only query to DuckHaven,
it works on any attached catalog, and — unlike the `information_schema` views — it can be wrapped in a `SELECT`, so
tools can filter and project it:

```sql
DESCRIBE analytics.analytics.events;
SELECT column_name, column_type FROM (DESCRIBE analytics.analytics.events);
```

It reports DuckDB type names, which is what a `SELECT` on the same table returns —
`DECIMAL(18,4)`, `TIMESTAMP WITH TIME ZONE`, `VARCHAR[]`, `STRUCT(city VARCHAR, zip INTEGER)`, and so on. `PRAGMA
table_info('catalog.schema.table')` returns the same information in a different shape, but only `DESCRIBE` composes
into a larger query, so prefer it.

The same spelling is what a query result reports in its `column_schema`
(see [Column types](../concepts/query-execution.md#column-types)), so the type you read from `DESCRIBE` and the type you
get back with your rows are the same string.

#### Types whose *values* are degraded in results

Result rows travel as Parquet from the agent and then as JSON to the client. `column_schema` always reports the query's
real type, but for a few types the values you receive are not exact:

| Type | What you receive |
|---|---|
| `HUGEINT`, `UHUGEINT` | A JSON number — **precision is lost**; the value passes through `DOUBLE` in the Parquet file |
| `DECIMAL(p, s)` | A JSON number — **precision is lost** beyond what a float64 holds (`1.5`, not `"1.5000000000"`) |
| `BLOB` | Lowercase hex text (`"616263"`) |
| `BIT` | Bit-string text (`"0101"`) |
| `ENUM(…)` | The label text (`"e"`) |
| `UUID` | The canonical hyphenated string |
| `INTERVAL` | An ISO-8601 duration (`"P3D"`) |
| `DATE`, `TIME`, `TIMESTAMP`, `TIMESTAMPTZ` | ISO-8601 strings |

Nested types (`LIST`, `STRUCT`, `MAP`, `ARRAY`) come back as JSON arrays and objects with the same per-element rules
applied. A fixed-size `INTEGER[2]` is *reported* as `INTEGER[2]` but arrives as an ordinary JSON array.

The two precision-losing rows are the ones to watch: `column_schema` will correctly tell you a column is
`DECIMAL(38,10)`, but the number you get alongside it has already been through a float. Exact decimal round-tripping is
not yet available.

!!! warning "`information_schema.columns` does not work for Iceberg tables"
    DuckDB cannot introspect the columns of an attached Iceberg REST table through `information_schema.columns`,
    `duckdb_columns()`, or the `column_names`/`column_types` of `SHOW ALL TABLES`. Instead of the real columns you get a
    single placeholder row — column name `__`, type `UNKNOWN`. Use `DESCRIBE`.

The reason is that DuckDB loads an Iceberg table's schema **lazily**, one table at a time, on first use: populating
those views eagerly would mean one `LoadTable` request to Polaris for every table in the catalog. Querying them does not
trigger that load ([duckdb/duckdb-iceberg#1146](https://github.com/duckdb/duckdb-iceberg/issues/1146)), and the
maintainers have [declined](https://github.com/duckdb/duckdb-iceberg/issues/515) to make it eager, placing the real fix
in DuckDB core. It is therefore **not** something a DuckHaven or DuckDB upgrade is expected to resolve, and you should
not write tooling against it.

Worse than being empty, the view is *inconsistent*. Once something in the same connection has touched a table, that
table's columns appear correctly while every other table still shows the placeholder. A single query opens a fresh
connection, so there you always get placeholders; a [SQL session](../concepts/query-execution.md) holds its connection
across statements, so there you can get a result that is correct for the tables you happen to have queried already and
wrong for the rest — which is the more dangerous failure, because it looks like it works.

Constraint and key views (`table_constraints`, `key_column_usage`) are absent for an unrelated reason: Iceberg has no
enforced primary or foreign keys.

For Iceberg-native facts — snapshot history, file and manifest details — use the `iceberg` table functions:

```sql
SELECT * FROM iceberg_snapshots('analytics.analytics.events');
SELECT * FROM iceberg_metadata('analytics.analytics.events');
```

See [Snapshots & time travel](../guides/snapshots-time-travel.md) for more on snapshot history.

## Rejected statements

Anything that could break out of the per-query sandbox is rejected, including:

`ATTACH` / `DETACH`, `COPY` / `EXPORT`, `INSTALL` / `LOAD`, `SET`, `CALL`, `EXPLAIN`, `VACUUM`, and transaction control.

This includes the configuration-setting form of `PRAGMA` — `PRAGMA <name> = <value>`, which DuckDB treats as a `SET` —
since it could widen the per-query sandbox. The read-only `PRAGMA`s that return rows are allowed, as described above.

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
