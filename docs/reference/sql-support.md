# SQL support

DuckHaven validates every statement against an allowlist at the API boundary (parse-only — the control plane never
executes user SQL) before dispatching it to an [agent](../concepts/agents.md).

## Allowed statements

| Category | Statements |
|---|---|
| Data | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE` |
| Catalog DDL | `CREATE`, `ALTER`, `DROP` (schemas and tables) |

These run on the agent against the workspace's attached Polaris REST catalog. A single `SELECT` is materialized to
Parquet and returned as a result grid; other statements run and report status without a grid.

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
