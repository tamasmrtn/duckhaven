---
type: how-to
---

# Read the session audit trail

Use the **Connections** screen to see which SQL sessions are holding agents, what a dbt or dlt run did, and why a
session ended.

When dbt or dlt runs against DuckHaven it opens a [SQL session](sql-sessions.md) and runs many statements through it;
one `dbt run` can be hundreds. Read one at a time in History they are noise. Read as one session they are a build you
can follow, time and attribute.

## Before you begin

- **You need:** membership of the workspace. Force-closing another member's session needs the `queries:admin`
  permission.
- **Enabled by:** `SQL_SESSIONS_ENABLED=true`. Without it, the screen says "SQL connections are not enabled".

## See which sessions are holding agents

Open **Connections** in the left navigation. The **Live** tab lists sessions that are still open.

An open session holds memory on its agent for its whole life, not only while a statement runs. A forgotten dbt shell,
or a client that crashed without closing, lowers how much work that agent accepts. If queries queue on an agent that
looks idle, look here first.

Each row shows the **Principal** who opened it, the **Client**, the **Agent** holding it, its **Catalog**, how many
**Statements** it has run, and when it was **Last active**. An old **Last active** with few statements is the usual
sign of a leaked session.

**Client** is the tool, such as `dbt-duckhaven 0.5.0` or `dlt-duckhaven 0.6.0`, read from the connection's
`User-Agent` when the session opened. Sessions from `duckhaven-sql-connector` before 0.3.0 show the connector instead
of the tool that used it.

A **pending** session has no agent: [compute is starting](sql-sessions.md#cold-start), and the session takes the
agent when it registers. One or two during a cold start are normal. Many mean compute is slow to arrive or is not
arriving; check **Compute** for an agent stuck `provisioning`.

### Force-close a session

With `queries:admin`, each live row has **Force close**. It tells the agent to drop the connection and release its
memory. Anything in flight fails, and the client must open a new session, so a running `dbt run` reports errors. Use it
for a session whose client is gone, not to interrupt work someone is waiting on.

## Follow what a session did

The **All** tab lists every session in the workspace, newest first. Click a row to open it.

The header shows the principal, client, agent and catalog, when the session opened, was last active and closed, and its
**Staging prefix**. Below it, the statement list shows every statement in the order it ran, with its status, rows,
duration and error. Read it top to bottom to see where a build slowed down or which statement failed first. Click a
statement to open its [profile](query-profiles.md).

## Find out why a session ended

**Ended because** gives the reason:

| Shown as | What happened |
|---|---|
| closed by client | The client closed it. A healthy `dbt run` ends this way |
| reaped — idle | No statement for the idle timeout: the client crashed, was killed or forgot to close. Look into it if it is routine |
| reaped — max lifetime | The session outlived the maximum lifetime. Expected for long jobs; if a normal build hits it, raise `SQL_SESSION_MAX_LIFETIME_S` |
| agent disconnected | The agent dropped; everything in flight failed. Look at the agent, not the client |
| open timed out, failed to open | The session never became usable. The session's **Error** has the detail |
| compute did not start in time, no compute became available | [Elastic compute](../compute/elastic-compute.md) did not start an agent in time, or at all |
| reaped by agent | The agent expired a session the API had lost track of |

## Go from a statement to its session

History lists every session statement too. To connect the two:

1. In **History**, set the origin filter to **Connection** to keep only statements run in sessions.
2. Click a statement's session id in the **Connection** column to open its session.

The **Client** column names the tool for each statement, the same as its session's.

## If it goes wrong

### "SQL connections are not enabled"

Sessions are off on this deployment. An operator sets `SQL_SESSIONS_ENABLED=true` in `.env` and runs
`docker compose up -d`.

### Force close is missing

You do not hold `queries:admin`. Ask an admin, or ask the session's owner to stop their client.

### The statements do not say which dbt model they belong to

A client can attach **tags** to each statement, such as `"tags": {"dbt.model": "orders"}`, which History shows beneath
the SQL. `dbt-duckhaven` and `dlt-duckhaven` do not send tags, so for their runs the session is the unit you can
attribute: one session is one run of the tool.

## Related

- [SQL sessions](sql-sessions.md) — what a session is and what it holds.
- [SQL sessions API](../reference/sql-sessions-api.md#close-reasons) — the close reasons as the API reports them.
- [Run queries](run-queries.md#find-an-earlier-run) — History.
