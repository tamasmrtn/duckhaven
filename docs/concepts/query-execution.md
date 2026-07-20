# Query execution

This page explains what happens between pressing **Run** and seeing results — the path a query takes through the
control plane and an [agent](agents.md).

## Lifecycle

1. The browser submits SQL plus a chosen agent to the control plane.
2. The API checks workspace membership, validates the SQL against the allowlist (parse-only), and confirms the agent is
   connected and compatible with the workspace backend.
3. The API dispatches the query over the agent's WebSocket.
4. The agent **admits** the query (sizing memory, queueing if needed), executes it, materializes the result to Parquet,
   and reports back.
5. The browser polls for status, then pages result rows on demand.

## The SQL allowlist

Only data statements (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`) and catalog DDL (`CREATE`, `ALTER`, `DROP`) reach
an agent. Sandbox escapes such as `ATTACH`, `COPY`, `LOAD`, `SET`, and `PRAGMA` are rejected at the API boundary. See
[SQL support](../reference/sql-support.md).

## Admission control and concurrency

Each agent runs queries under admission control so it never oversubscribes memory and gets OOM-killed. The agent's
memory budget is cgroup-aware (it respects a container/pod limit). Capacity is sized one of two ways:

- **`auto` (default)** — the agent runs `EXPLAIN` and estimates a query's peak memory from the optimizer's plan, then
  reserves a proportional "T-shirt" slice; cheap queries pack in, heavy ones queue.
- **Static slot ladders** — a fixed weighted split (`single`, `equal_2`, `decaying_2`, `decaying_3`).

The profile is switchable at runtime and applies per agent. See
[Runbook §6](../operations/runbook.md#6-query-queueing-concurrency) and [Scaling compute](../operations/scaling.md).

## Scheduled vs. interactive runs

Most queries are **interactive**: a person presses Run, picks the agent, and waits
for rows. A query can also run **unattended** on a cron [schedule](../guides/schedule-queries.md).
A scheduled run takes the exact same path described above — same allowlist, same
agent dispatch, same admission control — with two differences: there is no waiting
user (it is dispatched fire-and-forget; results stream back and are recorded), and
it is tagged `origin="scheduled"` so History can distinguish it from a query someone
ran by hand. A leader-elected loop in the control plane drives schedules, so exactly
one replica dispatches a given due schedule.

## Results and profiles

Results are materialized as Parquet **on the executing agent**; the control plane fetches and decodes pages on demand,
so large results are never loaded whole. After each run the agent captures DuckDB's per-operator execution profile —
see [Read query profiles](../guides/query-profiles.md). Queries can be cancelled mid-flight, and a wall-clock timeout is
enforced agent-side.

### Column types

A finished query reports its **column types** alongside its rows, so a client never has to guess them from the values.
The agent reads the types off the DuckDB result before writing the Parquet file, and reports them with the completion —
which means they are available from the query's status as soon as it is done, without fetching a page. Both
`GET /api/queries/{id}` and `GET /api/queries/{id}/rows` carry them as `column_schema`, a list of
`{"name": …, "type": …}` entries.

The `type` is spelled exactly the way DuckDB itself prints a logical type — the same string
[`DESCRIBE`](../reference/sql-support.md) returns. That spelling is self-describing and complete, including
parameterized and nested types such as `DECIMAL(38,10)`, `STRUCT(a INTEGER, b VARCHAR)`, `ENUM('e', 'f')` and
`INTEGER[2]`, so a client can map a column without a second lookup.

Capturing the types *before* materialization matters, because DuckDB's Parquet writer does not preserve all of them: a
`HUGEINT` column becomes `DOUBLE` in the file, `ENUM` and `BIT` become `VARCHAR`, and a fixed-size `INTEGER[2]` becomes
a variable-length `INTEGER[]`. Reading the types back out of the result file would therefore report the file's types
rather than the query's.

!!! note "Values are still JSON"
    `column_schema` describes the types; the row values themselves are still JSON-encoded. `DECIMAL` and `HUGEINT`
    values arrive as JSON numbers and lose precision at the extremes — the type tells you what the column *is*, but
    exact decimal round-tripping is not yet available. `column_schema` is `null` for statements that produce no result
    grid (DDL and DML) and for queries run by an agent older than this feature.
