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
