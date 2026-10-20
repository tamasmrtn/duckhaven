---
type: how-to
---

# Connect dbt

Run a dbt project on DuckHaven with the `dbt-duckhaven` adapter, so models build in your catalogs on DuckHaven's agents.

The adapter reuses `dbt-duckdb`'s SQL and macros and changes only the connection: each dbt thread opens a
[SQL session](sql-sessions.md) through the DuckHaven API, authenticates with a token, and every statement is checked and
recorded like any other query. A run shows up on the **Connections** screen as one session per thread.

## Before you begin

- **You need:** a [service-account token](../access/service-accounts.md) (`dh_pat_…`) whose account is a `writer` in
  the workspace, and Python 3.11 or later on the machine that runs dbt.
- **Enabled by:** `SQL_SESSIONS_ENABLED=true` on the DuckHaven server.
- **Applies to:** Iceberg and DuckLake catalogs. Iceberg catalogs do not support `view` models; see
  [What works](#what-works).

This page uses a catalog `sales` with the table `sales.analytics.orders`, in the workspace `analytics`.

## 1. Install the adapter

```bash
pip install dbt-duckhaven
```

This installs dbt Core, `dbt-duckdb` and the `duckhaven-sql-connector` it uses.

## 2. Configure the profile

Add a profile to `profiles.yml`, in your project or in `~/.dbt/`:

```yaml
sales_marts:
  target: dev
  outputs:
    dev:
      type: duckhaven
      host: https://duckhaven.example.com
      workspace: analytics
      token: "{{ env_var('DUCKHAVEN_PAT') }}"
      catalog: sales
      schema: marts
      threads: 2
```

| Field | Meaning |
|---|---|
| `host` | The DuckHaven address |
| `workspace` | The workspace slug |
| `token` | The service account's token; read it from the environment rather than writing it in the file |
| `catalog` | The catalog models are built in: dbt's *database* |
| `schema` | The schema models are built in. dbt creates it if it does not exist |
| `agent` | Optional agent id. Leave it out to let DuckHaven choose |
| `threads` | Sessions dbt opens at once. Each holds memory on the agent for the run; 2–4 suits most agents |

Point `profile:` in `dbt_project.yml` at `sales_marts`, and export the token:

```bash
export DUCKHAVEN_PAT=dh_pat_…
```

## 3. Check the connection

```bash
dbt debug
```

The end of the output confirms the connection:

```text
Connection:
  host: https://duckhaven.example.com
  workspace: analytics
  agent: None
  catalog: sales
  database: sales
  schema: marts
Registered adapter: duckhaven=0.5.0
  Connection test: [OK connection ok]

All checks passed!
```

## 4. Build

A model reads your tables by their full names:

```sql
-- models/customer_revenue.sql
select
    customer,
    count(*) as orders,
    sum(amount) as revenue
from sales.analytics.orders
group by customer
```

```bash
dbt build
```

```text
1 of 3 START sql table model marts.customer_revenue ............................ [RUN]
1 of 3 OK created sql table model marts.customer_revenue ....................... [OK in 0.74s]
3 of 3 START test unique_customer_revenue_customer ............................. [RUN]
2 of 3 START test not_null_customer_revenue_customer ........................... [RUN]
3 of 3 PASS unique_customer_revenue_customer ................................... [PASS in 0.88s]
2 of 3 PASS not_null_customer_revenue_customer ................................. [PASS in 1.07s]

Done. PASS=3 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=3
```

## Check that it worked

Query the model in a worksheet:

```sql
SELECT * FROM sales.marts.customer_revenue ORDER BY revenue DESC;
```

| customer | orders | revenue |
|---|---|---|
| initech | 1 | 310.00 |
| acme | 2 | 162.25 |
| globex | 1 | 75.50 |

The run's sessions appear on the **Connections** screen with the client `dbt-duckhaven`, and each statement in History;
see [Read the session audit trail](session-audit.md).

## What works

- **Materializations:** `table`, `seed`, `incremental` and `ephemeral`, and snapshots with the `timestamp` and `check`
  strategies. Generic and singular tests.
- **Incremental strategies:** `append`, `delete+insert`, `merge` and `microbatch`. `merge` and `microbatch` need an
  agent on DuckDB 1.5.3 or later; the adapter does not offer them otherwise.
- **Not supported:** `view` models on Iceberg catalogs (use `table`), and Python models.

The adapter's README lists every option and limit:
[`dbt-duckhaven`](https://github.com/tamasmrtn/duckhaven-clients/tree/main/packages/dbt-duckhaven).

## If it goes wrong

### `[404] SQL sessions are not enabled`

The server has sessions off. An operator sets `SQL_SESSIONS_ENABLED=true` in `.env` and runs `docker compose up -d`.

### `[401] Unauthorized`

The token is wrong, expired or revoked, or the service account is deactivated. Check `DUCKHAVEN_PAT`, then issue a new
token if needed; see [Service accounts & tokens](../access/service-accounts.md).

### `[503] No connected agent available`

No agent is connected, for example just after the server restarted. Wait for an agent to show **Running** under
**Compute**, then run again. With [elastic compute](../compute/elastic-compute.md), the first connection waits up to
five minutes for an agent to start instead.

### `Agent not found`, or `requires the 'use' tier`

You set `agent` to an agent the service account may not use. A restricted agent it holds no grant on is hidden, so it
reads as not found. Remove `agent` to let DuckHaven choose, or ask for the `use` tier; see
[Agent access](../access/agent-access.md).

### `Not authorized (writer) on …`

The catalog is [scoped](../access/scoped-access.md) and the service account has no `writer` grant there. Ask a
workspace owner for one.

## Related

- [SQL sessions](sql-sessions.md) — what each dbt thread holds.
- [Load data with dlt](load-data-with-dlt.md) — load raw data for dbt to transform.
- [Import dbt lineage](../metadata/import-dbt-lineage.md) — show your models' lineage in DuckHaven.
