---
type: troubleshooting
---

# Troubleshoot queries

Find the message you see in the results pane, in History, or in a tool's error, then follow what to do.

The worksheet shows a query's error in place of its result grid, and History lists the failed run with the same error.
API clients get it as `message` in the [error body](../reference/errors.md); dbt and dlt print it after the status
code, as in `[404] SQL sessions are not enabled`.

## "Disallowed statement type(s): …"

**What it means.** The statement is not one a query may run, such as `ATTACH`, `SET` or `EXPLAIN`. It was refused
before reaching an agent, with **422** `sql_not_allowed`.

**What to do.** Check [SQL support](../reference/sql-support.md#allowed-statements) for what is allowed. To see a
query's plan, run it and open its [profile](query-profiles.md). Session-level commands such as `USE` and `SET timezone`
work only in a [SQL session](sql-sessions.md).

## "… is not permitted" in a SQL session

**What it means.** A session statement broke the [session policy](sql-sessions.md#statement-policy), with **422**
`statement_not_allowed`. The message names the rule, such as `SET memory_limit is not permitted (allowed: schema,
search_path, time zone, timezone)`, or a `COPY` or file read outside the session's staging prefix.

**What to do.** Use only the statements the policy allows; see
[SQL support](../reference/sql-support.md#allowed-statements). Stage files through the session's
[staging files](../reference/sql-sessions-api.md#staging-files) before reading them.

## "Not authorized (reader) on ⟨catalog⟩.⟨schema⟩.⟨table⟩"

**What it means.** The catalog is scoped and you have no grant of that tier on the table.

**What to do.** See [Troubleshoot sign-in and
access](../access/troubleshooting.md#not-authorized-reader-on-catalogschematable).

## "Agent not connected"

**What it means.** The agent the worksheet runs on is offline, and DuckHaven cannot start it. The run is recorded as
failed.

**What to do.** Click **Switch agent** in the results pane, or choose another agent in the agent chip. If every agent
is offline, see [Troubleshooting the deployment](../operations/troubleshooting.md).

## "No connected agent available"

**What it means.** A [SQL session](sql-sessions.md) could not open because no agent is connected, typically right
after the server or the agent restarted.

**What to do.** Wait until an agent shows **Running** under **Compute**, then retry. With
[elastic compute](../compute/elastic-compute.md), DuckHaven starts an agent instead of answering this.

## "agent_id is required"

**What it means.** No agent is connected, elastic compute is off, and the request named no agent.

**What to do.** Start or connect an agent; see [Add an agent](../compute/add-agent.md).

## "Compute is starting; retry shortly."

**What it means.** [Elastic compute](../compute/elastic-compute.md) is starting an agent, and it did not come up within
the wait. The answer is **503** `compute_starting` with a `Retry-After` header.

**What to do.** Retry after the `Retry-After` seconds; the retry lands on the agent that is starting. The DuckHaven
clients do this for you for up to five minutes.

## "queue full"

**What it means.** The agent's queue of waiting queries is full (`MAX_QUEUE_DEPTH`, 100 by default), so it refused the
query.

**What to do.** Retry when the agent is less busy. If it happens often, the agent is too small for its load: add an
agent or a larger one; see [Scaling compute](../compute/scaling.md).

## "queued timeout"

**What it means.** The query waited in the agent's queue longer than `QUEUED_TIMEOUT_S` allows.

**What to do.** As for "queue full": retry later, and add capacity if it repeats. The agent's
[Monitoring](../compute/agent-monitoring.md) tab shows how long queries wait.

## "query exceeded statement timeout"

**What it means.** The query ran longer than its timeout: ten minutes by default, or the worksheet's own setting.

**What to do.** Raise the worksheet's timeout under the toolbar's settings icon, or `timeout_s` in the API, or make
the query cheaper; its [profile](query-profiles.md) shows where the time went.

## "Out of Memory Error: …"

**What it means.** The query needed more memory than the agent could give it, even after spilling to disk.

**What to do.** Run it when the agent is less busy, reduce what it holds at once (fewer columns, an earlier filter, a
smaller join), or run it on a larger agent. See [What a query is actually
given](query-execution.md#what-a-query-is-actually-given).

## "The agent did not answer in time."

**What it means.** A describe or metadata request reached the agent, but the agent did not reply in time; **504**
`upstream_timeout`.

**What to do.** Retry. If it repeats, the agent is overloaded or unhealthy; check its
[Monitoring](../compute/agent-monitoring.md) tab.

## "These results have expired: agents keep them for 24 hours. Run the query again."

**What it means.** The result file was deleted after its retention period (**410**).

**What to do.** Run the query again.

## "The agent that ran this query is offline, so its results can't be read until it reconnects."

**What it means.** Results stay on the agent that ran the query, and that agent is offline.

**What to do.** Wait for the agent to reconnect, or run the query again on another agent.

## "You don't have access to the data this query read, so its results can't be shown."

**What it means.** A [scoped catalog](../access/scoped-access.md) refuses the result: you lack a grant on a table the
query read.

**What to do.** See [Troubleshoot sign-in and access](../access/troubleshooting.md).

## "A storage backend migration is in progress; the catalog is read-only."

**What it means.** The catalog is moving to another storage backend, so writes are refused (**409**
`catalog_read_only`). Reads work.

**What to do.** Wait for the migration to finish; see
[Migrate a catalog's storage](../data/migrate-catalog-storage.md).

## "IO Error: Could not connect to server …" under load

**What it means.** A burst of concurrent queries fails with errors naming the object store, the store itself is
healthy, and the failures clear within about a minute. The agent ran out of outbound ports, which happens when it does
not reuse object-store connections.

**What to do.** Run an agent image that reuses connections; see
[Connection reuse](../data/storage-backends.md#connection-reuse).

## "SQL sessions are not enabled"

**What it means.** A tool tried to open a [SQL session](sql-sessions.md) on a server with sessions off (**404**).

**What to do.** An operator sets `SQL_SESSIONS_ENABLED=true` in `.env` and runs `docker compose up -d`.

## "session is closed"

**What it means.** The session ended, because the client closed it, it was idle or too old, or its agent
disconnected, and the statement was refused (**409** `session_not_open`). The session's **Ended because** on the
Connections screen says which.

**What to do.** Open a new session. dbt and dlt do this on their next run.

## Related

- [Errors](../reference/errors.md) — every error code.
- [Run queries](run-queries.md) — the worksheet and History.
- [Troubleshoot sign-in and access](../access/troubleshooting.md)
- [Troubleshooting the deployment](../operations/troubleshooting.md)
