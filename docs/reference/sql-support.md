---
type: reference
---

# SQL support

DuckHaven runs DuckDB SQL, checked against an allowlist before an agent runs it; statements such as `ATTACH`, `INSTALL`
and `SET` are rejected.

The check only parses the SQL; the control plane never executes it. Two paths have different lists: **queries**, which
worksheets, schedules, the CLI and `POST …/queries` use, and [SQL sessions](../querying/sql-sessions.md), which dbt,
dlt and other drivers use and which need a few more statements. A rejected statement answers **422**
`sql_not_allowed` on the query path and `statement_not_allowed` in a session; nothing reaches an agent.

## Allowed statements

Each row is checked by a unit test against both gates, so this table is what the API enforces.

| Statement | Example | Queries | SQL sessions |
|---|---|---|---|
| Read | `SELECT * FROM sales.analytics.orders` | Yes | Yes |
| Insert | `INSERT INTO sales.analytics.orders VALUES (5, 'umbrella', 42.00, DATE '2026-10-01')` | Yes | Yes |
| Update | `UPDATE sales.analytics.orders SET amount = 40.00 WHERE order_id = 5` | Yes | Yes |
| Delete | `DELETE FROM sales.analytics.orders WHERE order_id = 5` | Yes | Yes |
| Merge | `MERGE INTO sales.analytics.orders t USING sales.analytics.staged s ON t.order_id = s.order_id WHEN MATCHED THEN UPDATE SET amount = s.amount` | Yes | Yes |
| Truncate | `TRUNCATE sales.analytics.orders` | Yes | Yes |
| Create a table | `CREATE TABLE sales.analytics.daily AS SELECT ordered_on, sum(amount) AS revenue FROM sales.analytics.orders GROUP BY 1` | Yes | Yes |
| Create a schema | `CREATE SCHEMA sales.staging` | Yes | Yes |
| Change a table | `ALTER TABLE sales.analytics.orders ADD COLUMN note VARCHAR` | Yes | Yes |
| Drop | `DROP TABLE sales.analytics.daily` | Yes | Yes |
| Describe | `DESCRIBE sales.analytics.orders` | Yes | Yes |
| Summarize | `SUMMARIZE sales.analytics.orders` | Yes | Yes |
| List tables | `SHOW ALL TABLES` | Yes | Yes |
| Row-returning `PRAGMA` | `PRAGMA table_info('sales.analytics.orders')` | Yes | Yes |
| Other row-returning `PRAGMA` | `PRAGMA version` | Yes | Yes |
| Pivot | `PIVOT sales.analytics.orders ON customer USING sum(amount)` | Yes | No |
| List databases | `SHOW DATABASES` | Yes | No |
| Set the catalog and schema | `USE sales.analytics` | No | Yes |
| Set the time zone | `SET timezone = 'UTC'` | No | Yes |
| Set the search path | `SET search_path = 'sales.analytics'` | No | Yes |
| Transactions | `BEGIN TRANSACTION` | No | Yes |
| Load a staged file | `COPY sales.analytics.orders FROM '⟨staging prefix⟩/orders.parquet'` | No | Yes |
| Read a staged file | `SELECT * FROM read_parquet('⟨staging prefix⟩/orders.parquet')` | Yes | Yes |
| Read any file by path | `SELECT * FROM read_parquet('s3://other-bucket/data.parquet')` | Yes | No |
| Attach a catalog the workspace binds | `ATTACH 'sales' AS sales` | No | Yes |

Notes on the rows above:

- **`TRUNCATE`** is DuckDB's spelling of `DELETE FROM` without a `WHERE`, and runs the same plan. On Iceberg it is not
  the cheap metadata operation the name suggests elsewhere: emptying a table writes delete files in proportion to its
  size. To discard a large table, drop and recreate it.
- **Introspection** (`DESCRIBE`, `SHOW`, `SUMMARIZE`, row-returning `PRAGMA`s) returns a result grid like a `SELECT`
  and changes nothing. `SUMMARIZE` scans the table, so it needs read access to the rows, not just the schema.
- **Session statements.** `SET` accepts only `timezone`, `search_path` and `schema`. `COPY` and file reads in a
  session are confined to the session's own staging prefix; see
  [Staging files](../querying/sql-sessions.md#staging-files).
- **Reading files by path** on the query path works in a workspace without a scoped catalog, limited to what the agent's
  network can reach. A workspace with a [scoped catalog](../access/scoped-access.md#files-are-read-through-tables)
  refuses it, because a path names no table to check grants against.
- **Writes and roles.** In an open catalog any member can run these statements; in a scoped catalog each table needs a
  grant. See [How access works](../access/how-access-works.md).

## Rejected statements

Statements that could change the agent's configuration, reach outside the workspace's catalogs, or bypass the
checks above are rejected on both paths:

| Statement | Example | Queries | SQL sessions |
|---|---|---|---|
| Explain | `EXPLAIN SELECT 1` | No | No |
| Attach another database | `ATTACH 'other.db' AS other` | No | No |
| Detach | `DETACH sales` | No | No |
| Copy to a local file | `COPY sales.analytics.orders TO '/tmp/orders.csv'` | No | No |
| Export | `EXPORT DATABASE '/tmp/export'` | No | No |
| Install or load an extension | `INSTALL httpfs` | No | No |
| | `LOAD httpfs` | No | No |
| Other settings | `SET memory_limit = '1GB'` | No | No |
| Reset a setting | `RESET timezone` | No | No |
| `PRAGMA` that sets something | `PRAGMA memory_limit = '1GB'` | No | No |
| | `PRAGMA disable_verification` | No | No |
| Call | `CALL pragma_version()` | No | No |
| Vacuum | `VACUUM` | No | No |
| Checkpoint | `CHECKPOINT` | No | No |
| Prepared statements | `PREPARE p AS SELECT 1` | No | No |
| | `EXECUTE p` | No | No |
| Create or drop a secret | `CREATE SECRET s3_creds (TYPE s3, …)` | No | No |
| | `DROP SECRET s3_creds` | No | No |

The agent sets up the storage credentials each catalog needs, so a query cannot create or drop a secret of its own.

To see a query's plan, open its [profile](../querying/query-profiles.md) after it runs.

### Rejected functions

Some functions are rejected inside any statement, in every workspace, on both paths:

- **Functions that run SQL, or read a table, named inside a string**: `query()`, `query_table()`,
  `json_execute_serialized_sql()`, `duckdb_table_sample()`, and the `histogram()` and `histogram_values()` table
  functions (the `histogram()` aggregate is fine). Access is checked against the tables a statement names, and a table
  named inside a string cannot be checked. Name the table in the query instead.
- **Functions that expose storage credentials**, such as `duckdb_secrets()` and `iceberg_load_table_response()`.
- **Functions that change a catalog, or the engine every user of an agent shares**: `checkpoint()`, the Iceberg
  property setters, DuckLake's `merge_adjacent_files()` and `set_option()` catalog methods, and the logging and
  profiling switches.
- **Functions that reach another database**, such as `postgres_query()`, and DuckLake's `ducklake_*` maintenance
  functions.

## Addressing catalogs

Every catalog the workspace attaches can be used in a query. A name with fewer than three parts (`analytics.orders` or
`orders`) resolves in the worksheet's **active catalog**. Name another catalog, or join across catalogs, with the full
`catalog.schema.table`:

```sql
SELECT o.order_id, c.region
FROM sales.analytics.orders o
JOIN crm.public.customers c ON c.name = o.customer;
```

A query attaches only the catalogs it names; see
[which catalogs a query attaches](../querying/query-execution.md#which-catalogs-a-query-attaches).

## Inspecting metadata (`information_schema`)

Every query can read DuckDB's `information_schema`, a set of read-only views that describe the attached catalogs. The
views span every catalog the query attaches and are computed when you query them. Narrow them to one catalog with
`table_catalog`:

```sql
-- Tables and views in a catalog
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_catalog = 'sales';

-- Schemas in a catalog
SELECT schema_name
FROM information_schema.schemata
WHERE catalog_name = 'sales';
```

!!! warning "Not available in a workspace with a scoped catalog"
    Because the views span every attached catalog, they cannot be filtered by grant. They are rejected in any workspace
    that has at least one scoped catalog, even from a worksheet whose active catalog is open, and even with a
    `table_catalog` filter. The same applies to every other way of asking the engine to list objects: the `info_schema`
    spelling, any `SHOW` statement, the `duckdb_tables()` family, the listing `PRAGMA`s, functions that name their table
    in a string (`pragma_table_info()`, `iceberg_snapshots()`), DuckLake's catalog methods (`lake.snapshots()`), and
    direct file reads. See [Finding objects in a scoped
    catalog](../access/scoped-access.md#finding-objects-in-a-scoped-catalog).

### Columns and types: use `DESCRIBE`

`DESCRIBE` is the supported way to read a table's columns and types. It works on every catalog, and you can wrap it in
a `SELECT` to filter and project it:

```sql
DESCRIBE sales.analytics.orders;
SELECT column_name, column_type FROM (DESCRIBE sales.analytics.orders);
```

It reports DuckDB's type names, the same ones a `SELECT` on the table returns: `DECIMAL(18,4)`,
`TIMESTAMP WITH TIME ZONE`, `VARCHAR[]`, `STRUCT(city VARCHAR, zip INTEGER)`. A query result reports the same spelling
in its `column_schema` (see [Column types](../querying/query-execution.md#column-types)).
`PRAGMA table_info('catalog.schema.table')` gives the same facts in another shape, but only `DESCRIBE` composes into a
larger query.

#### Types whose values arrive in a different shape

Rows travel from the agent as Parquet, then to the client as JSON. `column_schema` always reports the query's real
type; for these types the JSON value is a string or another encoding:

| Type | What you receive |
|---|---|
| `BIGINT`, `UBIGINT`, `HUGEINT`, `UHUGEINT` | A JSON string with the exact value (`"9007199254740993"`), because a JSON number cannot hold every value. A `HUGEINT` with more than 38 digits fails the query; cast it to `VARCHAR` to get it as text |
| `DECIMAL(p, s)` | A JSON string with the exact value (`"1.5000000000"`) |
| `BLOB` | Lowercase hex text (`"616263"`) |
| `BIT` | Bit-string text (`"0101"`) |
| `ENUM(…)` | The label text (`"e"`) |
| `UUID` | The canonical hyphenated string |
| `INTERVAL` | An ISO 8601 duration (`"P3D"`) |
| `DATE`, `TIME`, `TIMESTAMP`, `TIMESTAMPTZ` | ISO 8601 strings |

Nested types (`LIST`, `STRUCT`, `MAP`, `ARRAY`) arrive as JSON arrays and objects, with the same rules for each element.
A fixed-size `INTEGER[2]` is reported as `INTEGER[2]` but arrives as an ordinary JSON array. To convert a string value
back to a number, read its column's type in `column_schema`; see [Result values](rest-api.md#result-values).

!!! warning "`information_schema.columns` does not work for Iceberg tables"
    For a table in an Iceberg catalog, `information_schema.columns`, `duckdb_columns()`, and the `column_names` and
    `column_types` of `SHOW ALL TABLES` return one placeholder row, column `__` of type `UNKNOWN`, instead of the real
    columns. Use `DESCRIBE`.

DuckDB loads an Iceberg table's schema only when a query first uses the table, one table at a time; filling these views
eagerly would mean one request to Polaris per table in the catalog. Querying the views does not trigger that load
([duckdb/duckdb-iceberg#1146](https://github.com/duckdb/duckdb-iceberg/issues/1146)), and the maintainers have
[declined](https://github.com/duckdb/duckdb-iceberg/issues/515) to make it eager, so do not build tools on it.

The views are also inconsistent within one connection: a table that something in the same connection has already
used shows its real columns, while every other table still shows the placeholder. A single query opens a new
connection, so it always gets placeholders. A [SQL session](../querying/sql-sessions.md) keeps its connection between
statements, so it can get answers that are right for the tables it has queried and wrong for the rest, which looks like
it works.

Iceberg has no enforced primary or foreign keys, so the constraint views (`table_constraints`, `key_column_usage`) are
empty.

For Iceberg's own facts, such as snapshot history and data files, use the `iceberg` table functions:

```sql
SELECT * FROM iceberg_snapshots('sales.analytics.orders');
SELECT * FROM iceberg_metadata('sales.analytics.orders');
```

See [Snapshots & time travel](../data/snapshots-time-travel.md). These functions name their table in a string, so they
are rejected in a workspace with a [scoped catalog](../access/scoped-access.md#finding-objects-in-a-scoped-catalog);
there, a table's **History** tab in the catalog browser shows its snapshots.

## Query parameters

A query can carry **placeholders** and send their values separately, the way a database driver does. DuckHaven turns
each value into a correctly typed and quoted literal, so your code never builds quoted SQL itself:

```json
{
  "sql": "SELECT * FROM sales.analytics.orders WHERE customer = ? AND ordered_on >= ?",
  "parameters": ["acme", {"type": "DATE", "value": "2026-01-01"}]
}
```

`parameters` works on `POST /api/workspaces/{workspace}/queries` and on SQL session statements. A statement uses one of
four placeholder styles:

| Style | `parameters` | Example |
|---|---|---|
| `?` | a list, in order | `WHERE a = ? AND b = ?` |
| `?1`, `?2` | a list, by position (reusable) | `WHERE a = ?1 OR b = ?1` |
| `$1`, `$2` | a list, by position (reusable) | `WHERE a = $1` |
| `$name` | an object | `WHERE region = $region` |

A value is a JSON string, number, boolean or null, or an object `{"type": …, "value": …}` that names the DuckDB type to
read it as. Use the object form for types JSON lacks: `DECIMAL(p,s)` (send the value as a string to keep every digit),
`DATE`, `TIME`, `TIMESTAMP`, `TIMESTAMPTZ`, `INTERVAL`, `UUID`, `JSON`, `BLOB` (base64-encoded), or a list such as
`BIGINT[]` with a JSON array as its value.

The values are written into the SQL before anything checks it. The allowlist, your grants and the
[result cache](../querying/result-cache.md) all see the statement exactly as it runs, so a parameter cannot reach
anything the same SQL with literals could not, and a repeated query with the same values can be answered from the
cache. History records this rendered statement.

The request is refused with **422** `invalid_parameters` when the parameters do not fit: more than one statement, mixed
placeholder styles, a missing or unused value, a type outside the list above, or a placeholder in `ORDER BY` or
`GROUP BY`, where DuckDB would read a value as a column position.

## Time-travel syntax

Query an Iceberg table as of an earlier snapshot with DuckDB's `AT` clause (see
[Snapshots & time travel](../data/snapshots-time-travel.md)):

```sql
SELECT * FROM analytics.events AT (VERSION => 7287998166701990000);
SELECT * FROM analytics.events AT (TIMESTAMP => '2026-05-01 00:00:00');
```

## Concurrency control command

A worksheet can change the selected agent's [concurrency profile](../compute/scaling.md) with a DuckHaven command, run
as its own statement:

```sql
SET duckhaven_concurrency = 'auto';   -- auto | single | equal_2 | decaying_2 | decaying_3
RESET duckhaven_concurrency;          -- back to the default
```

It applies to every query on that agent, not only yours, until the agent restarts, and it is recorded in History.

## Related

- [Run queries](../querying/run-queries.md) — the worksheet.
- [SQL sessions](../querying/sql-sessions.md) — the session path and its staging prefix.
- [Errors](errors.md) — `sql_not_allowed`, `statement_not_allowed` and `invalid_parameters`.
- [Troubleshoot queries](../querying/troubleshooting.md) — what a rejected statement looks like.
