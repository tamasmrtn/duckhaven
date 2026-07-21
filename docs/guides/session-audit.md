# Read the session audit trail

When dbt or dlt runs against DuckHaven it opens a [SQL session](../concepts/sql-sessions.md) and executes many
statements through it. A single `dbt run` can be hundreds of statements. Read one at a time in the query history they
are noise; read as one session they are a build you can follow, blame, and time.

The **Sessions** screen is where you do that. Open it from the left nav.

!!! note "Sessions must be enabled"
    The whole session surface is off unless the operator sets `SQL_SESSIONS_ENABLED=true` on the API. If it is off,
    Sessions says so instead of showing an empty list.

## Which sessions are running right now

The **Live** tab lists sessions that are still open. This is an operational view, not a historical one, and it exists
because **an open session holds one of its agent's admission slots for its entire life** — not just while a statement
is running. A forgotten dbt shell or a client that crashed without closing its connection quietly reduces how much
interactive work that agent can accept. If queries are queueing on an agent that looks idle, this is the first place to
look.

Each row shows who opened the session, which client did it, which agent is holding it, its active catalog, how many
statements it has run, and when it was last active. A session with an old **Last active** and a low statement count is
the classic leak.

The **Client** column is the tool itself — `dbt-duckhaven 1.2.0`, `dlt-duckhaven 0.4.1`. DuckHaven reads this from the
connection's `User-Agent` when the session opens, so it is recorded whether or not the client thought to identify
itself.

### Force-closing a session

If you hold the `queries:admin` permission, a **Force close** button appears on each live row. It tells the agent to
drop its held connection and release the admission slot.

This is not a graceful pause. Anything the session still has in flight fails, and the client must open a new session to
carry on — a mid-flight `dbt run` will report errors. Use it to reclaim capacity from a session whose client is
genuinely gone, not to interrupt work someone is waiting on.

## What a session did

The **All** tab lists every session in the workspace, newest first. Click any row — live or finished — to open it.

The header answers *who and where*: the principal, the client, the agent, the active catalog, the open/last-active/
closed timestamps, and the staging prefix the session was allowed to load from.

Below it is the **statement timeline**: every statement the session ran, numbered in **execution order** rather than
newest-first, with its status, row count, duration, and error. Reading top to bottom shows the build as it actually
happened — where it sped up, where it stalled, and which statement was the first to fail. Click any statement to open
it in the usual [query profile](query-profiles.md) view.

## Why a session ended

A finished session's status tells you it stopped; the **Ended because** column tells you why, which is usually the
question you actually have. In particular, "expired" covers two different stories that the UI keeps apart:

- **closed by client** — the client shut the session down cleanly. This is what a healthy `dbt run` looks like.
- **reaped — idle** — nobody ran a statement for the configured idle timeout. The client crashed, was killed, or
  forgot to close. Harmless once, worth investigating if it is routine.
- **reaped — max lifetime** — the session outlived the maximum lifetime, however busy it was. Expected for genuinely
  long jobs; if a normal build hits it, the cap is too low for your workload.
- **agent disconnected** — the agent holding the connection dropped. Everything in flight died with it, and this
  points at the agent, not the client.
- **open timed out** / **failed to open** — the session never became usable. Usually an overloaded or misconfigured
  agent; the session's **Error** field has the detail.

## Finding a session from the history

The [query history](../concepts/query-execution.md) still lists every statement individually — sessions do not hide
anything from it. Two additions connect the two views:

- The **origin filter** at the top of History narrows to **Interactive**, **Scheduled**, or **Session** runs. Picking
  **Session** leaves only statements that ran inside a session.
- Once session statements are in view, a **Session** column appears. Clicking a statement's session id jumps to that
  session, so you can go from "this one query looks wrong" to the whole build it came from in one click.

## What this does not tell you yet

Attribution stops at the tool. DuckHaven records that `dbt-duckhaven 1.2.0` opened a session and what SQL it ran, but
not which dbt model or invocation produced a given statement — the client cannot yet attach that context. Until it
can, the session boundary is the unit of attribution: one session is one client's run.
