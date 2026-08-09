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
  reserves a proportional "T-shirt" slice; cheap queries pack in, heavy ones queue. This applies to one-shot queries
  **and** to statements run inside a [SQL session](sql-sessions.md#pinning-lifetime-and-failure) — a session grows to
  fit each statement and shrinks back between them, rather than holding one size for its whole life.
- **Static slot ladders** — a fixed weighted split (`single`, `equal_2`, `decaying_2`, `decaying_3`). Slots are fixed,
  so a session under a static profile holds its whole slot for its lifetime.

The profile is switchable at runtime and applies per agent. See
[Runbook §6](../operations/runbook.md#6-query-queueing-concurrency) and [Scaling compute](../operations/scaling.md).

### What a query is actually given

A reservation has two parts, and they answer different questions.

**Required memory** is what the query must have in order to finish — the estimate above, snapped to a bucket. It is
what the admission gate blocks on, and it is never taken away once granted. The sum of every running query's required
memory always stays inside the agent's budget; that is the guarantee that stops the agent being OOM-killed.

**Elastic memory** is spare budget *lent* to a query on top of that. DuckDB's memory limit does not only cap operator
memory — it also sizes the cache DuckDB keeps of the Parquet files it has read from object storage. A query sized to
its operators alone has nowhere to keep that cache, so every scan goes back to the object store and re-decompresses
data it has already read. Lending it the agent's idle memory removes that cost, and costs other tenants nothing:
elastic memory is **revocable**, so the moment another query needs those bytes the agent takes them back (the lender
simply loses some cache) rather than making the newcomer queue. A query never waits on memory that is only being used
to cache with. Elastic memory belongs to `auto` alone — a static ladder's slot is a fixed contract, which is the reason
to choose one, so it neither grows nor shrinks. On a static profile a query gets exactly its slot's share, and a small
slot on a small agent may be too tight to cache with.

No query can take more than a **fair share** of the lendable memory — the agent's budget divided by the number of live
sessions. Without that bound the first query to ask takes everything free and every session behind it runs on the bare
idle minimum, which is far worse for everyone than an even split. The share is recalculated every time memory is handed
out, so it shrinks as sessions arrive and each holder gives the excess back when its next statement finishes.

### When the agent is full

Sometimes there is simply not enough memory to go round — twenty concurrent scans over a large table do not fit in a
small agent under any allocation. A query that cannot reach a workable fraction of what it asked for **waits** for room
rather than running in a size it cannot work in, because a query squeezed into the idle minimum spills to disk so hard
that it hurts everything else running beside it. It stops waiting as soon as memory frees up, when its own timeout
would be exceeded, or immediately if nothing else is running (in which case no memory is going to be released and
waiting could only make things slower).

Waiting is visible rather than mysterious: every query reports how long it spent waiting for memory in its
[profile](../guides/query-profiles.md), so a query that was slow because the agent was busy is distinguishable from one
that was slow because it was expensive. `STATEMENT_ADMISSION_WAIT_S` bounds the wait; setting it to `0` restores the
older behaviour of always running immediately at whatever size was available.

**Threads** are separate from both, and work the same way under **every** profile: each statement is given the agent's
full core count. Neither the `auto` estimator's buckets nor a static ladder's weights touch it — they divide memory. The
container's CPU quota is the real limit on how much CPU an agent can use, and the operating system shares it out
between concurrent queries, so handing one query fewer threads than the agent has cores only makes that query slower
without leaving anything extra for anyone else.

!!! note "Sessions keep their cache between statements"
    A [SQL session](sql-sessions.md) hands its *required* memory back after each statement but keeps its elastic
    grant, so consecutive statements against the same tables do not re-read them from object storage. An idle session
    holding cache never blocks another query — see above.

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
