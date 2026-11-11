---
type: how-to
---

# Use Python data tools

Query DuckHaven from Python and read results into pandas, Polars or a marimo notebook with `duckhaven-sql-connector`.

The connector is a [PEP 249](https://peps.python.org/pep-0249/) client of the DuckHaven API. It authenticates with a
token, and each statement it sends is checked against your grants and recorded in History like a worksheet run. It
returns rows as Python values, or a whole result as an Arrow table that pandas and Polars take over without converting
each value through Python.

## Before you begin

- **You need:** membership of the workspace, with any role, and a token (`dh_pat_…`): `dh auth login` issues one for
  you from [the command line](../getting-started/cli-quickstart.md), or an administrator issues a
  [service-account token](../access/service-accounts.md). Python 3.11 or later.
- **Applies to:** every deployment. Arrow results and exact decimals need a server with `api_version` 3; see
  [API versions](../reference/api-versions.md).
- **Enabled by:** nothing. With `mode="auto"` the connector runs each statement as a one-shot query, so it does not
  need [SQL sessions](sql-sessions.md).

This page reads `sales.analytics.orders` in the workspace `analytics`. To follow along, run these two statements one at
a time in a [worksheet](run-queries.md), which needs the `writer` role:

```sql
CREATE SCHEMA IF NOT EXISTS sales.analytics;
CREATE OR REPLACE TABLE sales.analytics.orders AS
SELECT * FROM (VALUES
    (1, 'acme',   120.50::DECIMAL(18,2), TIMESTAMPTZ '2026-11-02 09:15:00+00'),
    (2, 'globex',  75.25::DECIMAL(18,2), TIMESTAMPTZ '2026-11-02 11:40:00+00'),
    (3, 'acme',   310.00::DECIMAL(18,2), TIMESTAMPTZ '2026-11-03 08:05:00+00')
) AS t(id, customer, amount, ordered_at);
```

## 1. Install the connector

```bash
pip install "duckhaven-sql-connector[arrow]" pandas polars
```

The `arrow` extra adds pyarrow, which reads each result as one Arrow stream and provides `fetch_arrow_table()`. Without
it you get rows only.

## 2. Connect

Put the token in an environment variable, such as `export DH_TOKEN=dh_pat_…`, rather than in your code:

```python
import os

from duckhaven_sql_connector import connect

conn = connect(
    host="https://duckhaven.example.com",
    workspace="analytics",
    token=os.environ["DH_TOKEN"],
    catalog="sales",
    mode="auto",
)
cur = conn.cursor()
```

- `catalog` is the catalog that names without a catalog resolve in, as the **catalog** chip sets it in a worksheet.
- `mode="auto"` runs each statement as a one-shot query, and an idle connection holds nothing on the server. A
  statement that sets state for later ones, such as `SET`, `USE` or `CREATE TEMP TABLE`, opens a
  [SQL session](sql-sessions.md) the first time one is needed, and the connection stays on it. Without `mode="auto"`,
  `connect()` opens a session straight away.
- DuckHaven picks the agent as it does for a worksheet. Pass `agent="⟨agent id⟩"` to choose one.

## 3. Run a query

```python
cur.execute(
    "SELECT customer, sum(amount) AS total FROM sales.analytics.orders GROUP BY customer ORDER BY customer;"
)
print(cur.description[1][:2])
print(cur.fetchall())
```

```text
('total', 'DECIMAL(38,2)')
[('acme', Decimal('430.50')), ('globex', Decimal('75.25'))]
```

Each column's type is in `cur.description`, spelled as DuckDB prints it. Values come back as the matching Python type:
`Decimal` for `DECIMAL`, `datetime` for timestamps, `int` for every integer type. Pass values with `?` placeholders
rather than formatting them into the SQL:

```python
cur.execute(
    "SELECT id, amount FROM sales.analytics.orders WHERE customer = ? AND amount > ? ORDER BY id;",
    ["acme", 100],
)
print(cur.fetchall())
```

```text
[(1, Decimal('120.50')), (3, Decimal('310.00'))]
```

## 4. Read into pandas

```python
import pandas as pd

cur.execute("SELECT id, customer, amount, ordered_at FROM sales.analytics.orders ORDER BY id;")
df = cur.fetch_arrow_table().to_pandas(types_mapper=pd.ArrowDtype)
print(df.dtypes)
```

```text
id                            int32[pyarrow]
customer                     string[pyarrow]
amount            decimal128(18, 2)[pyarrow]
ordered_at    timestamp[us, tz=UTC][pyarrow]
dtype: object
```

`types_mapper=pd.ArrowDtype` keeps each column's type, so `amount` stays an exact decimal with its scale. Without it,
pandas stores decimals as Python `Decimal` objects in an `object` column, which most pandas operations handle slowly.

Use `fetch_arrow_table()` rather than `pd.read_sql()`: on a connection that is not SQLAlchemy or sqlite3, pandas warns
and turns every `DECIMAL` into a `float64`.

## 5. Read into Polars

```python
import polars as pl

cur.execute("SELECT id, customer, amount, ordered_at FROM sales.analytics.orders ORDER BY id;")
df = pl.from_arrow(cur.fetch_arrow_table())
print(df)
```

```text
shape: (3, 4)
┌─────┬──────────┬───────────────┬─────────────────────────┐
│ id  ┆ customer ┆ amount        ┆ ordered_at              │
│ --- ┆ ---      ┆ ---           ┆ ---                     │
│ i32 ┆ str      ┆ decimal[18,2] ┆ datetime[μs, UTC]       │
╞═════╪══════════╪═══════════════╪═════════════════════════╡
│ 1   ┆ acme     ┆ 120.50        ┆ 2026-11-02 09:15:00 UTC │
│ 2   ┆ globex   ┆ 75.25         ┆ 2026-11-02 11:40:00 UTC │
│ 3   ┆ acme     ┆ 310.00        ┆ 2026-11-03 08:05:00 UTC │
└─────┴──────────┴───────────────┴─────────────────────────┘
```

## 6. Query from a marimo notebook

marimo accepts any PEP 249 connection as the engine of a SQL cell. Create the connection in one cell:

```python
import os

import marimo as mo
from duckhaven_sql_connector import connect

conn = connect(
    host="https://duckhaven.example.com",
    workspace="analytics",
    token=os.environ["DH_TOKEN"],
    catalog="sales",
    mode="auto",
)
```

Then pick `conn` as the engine of a SQL cell, or call `mo.sql` with it:

```python
totals = mo.sql(
    "SELECT customer, sum(amount) AS total FROM sales.analytics.orders GROUP BY customer ORDER BY customer;",
    engine=conn,
)
```

`totals` is a Polars DataFrame when Polars is installed, and a pandas one otherwise. marimo builds it from the rows, so
the `total` column is a decimal whose precision marimo infers from the values, rather than the type in
`cur.description`.

marimo's data sources panel lists no tables for this connection, because it cannot browse a plain PEP 249 connection.
To see what you can read, call `conn.metadata(catalog="sales")`, which lists the schemas and tables your grants show.

## Check that it worked

Open **History** in the web app. Each statement the connector sent is listed as yours, with **Client**
`duckhaven-sql-connector`. To tell the runs of different scripts or notebooks apart, add tags; they appear beneath the
SQL:

```python
conn = connect(
    host="https://duckhaven.example.com",
    workspace="analytics",
    token=os.environ["DH_TOKEN"],
    catalog="sales",
    mode="auto",
    tags={"app": "forecast"},
)
cur = conn.cursor()
cur.execute("SELECT count(*) FROM sales.analytics.orders;", tags={"step": "load"})
```

This run is recorded with the tags `app: forecast` and `step: load`.

## If it goes wrong

### `[404] SQL sessions are not enabled` when you connect

The connection was opened without `mode="auto"`, so it tried to open a SQL session, and the server has sessions off.
Add `mode="auto"` to `connect()`.

### `NotSupportedError: this statement needs a SQL session, and SQL sessions are not enabled on this server`

In `auto` mode, a statement such as `SET`, `USE` or `CREATE TEMP TABLE` needs a SQL session, and the server has
sessions off. Write the statement without session state, for example with fully qualified names instead of `USE`, or
ask an operator to set `SQL_SESSIONS_ENABLED=true`. The connection keeps working for other statements.

### `NotSupportedError: pyarrow is required for Arrow results; install duckhaven-sql-connector[arrow]`

`fetch_arrow_table()` needs pyarrow. Run `pip install "duckhaven-sql-connector[arrow]"`.

### `UserWarning: pandas only supports SQLAlchemy connectable (engine/connection) …`

`pd.read_sql()` was given the connector's connection. It runs, but decimals arrive as floats. Read the result with
`cur.fetch_arrow_table().to_pandas(types_mapper=pd.ArrowDtype)` instead.

### `InterfaceError: [401] Unauthorized`

The token is wrong, expired or revoked. Check `DH_TOKEN`; `dh auth tokens` lists your tokens, and `dh auth login`
issues a new one.

### `ProgrammingError: [422] …`

DuckHaven refused the statement before it ran, and the message names the reason, such as
`[422] SET threads is not permitted (allowed: schema, search_path, time zone, timezone)`. What you can run is in
[SQL support](../reference/sql-support.md). A table in a [scoped catalog](../access/scoped-access.md) also needs a
grant before you can read it.

## Related

- [`duckhaven-sql-connector`](https://github.com/tamasmrtn/duckhaven-clients/tree/main/packages/duckhaven-sql-connector)
  — every connection option, error and type mapping.
- [Run queries](run-queries.md) — the same queries in a worksheet.
- [SQL sessions](sql-sessions.md) — what a session keeps between statements.
- [Connect dbt](connect-dbt.md) and [Load data with dlt](load-data-with-dlt.md) — the other DuckHaven Python tools.
