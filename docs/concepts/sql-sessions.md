# SQL sessions

A **SQL session** is a persistent database connection that DuckHaven holds open on one
[agent](agents.md) on your behalf, so a client can run many statements with connection-scoped state — temporary
relations, `USE catalog.schema`, `SET`, and multi-statement transactions — instead of the one-shot
[query execution](query-execution.md) path where every request is independent.

Sessions exist for external tools that expect a warehouse-style connection — a client that opens a connection, runs a
sequence of statements against it, and closes it. A client never talks to a compute node directly: it opens a session
through the DuckHaven API, and the API brokers each statement to the agent over the agent's own outbound WebSocket. No
inbound agent port is ever opened.

!!! note "Off by default"
    The session surface is disabled unless an operator sets `SQL_SESSIONS_ENABLED=true`. Enable it **only after
    deploying the hardened agent** (see [Sandboxing](#sandboxing) below): turning sessions on also enables the broader
    statement policy, so the container hardening must be in place first.

## Lifecycle

1. **Open** — `POST /api/workspaces/{ws}/sql/sessions`. The API picks the agent (an explicit `agent_id`, or an
   auto-picked compatible one), tells it to open and attach a DuckDB connection to the workspace's catalogs, and returns
   a `session_id` once the agent acknowledges. The session **pins** that agent: every later statement routes to it.
2. **Run statements** — `POST /api/sql/sessions/{id}/statements`. Each statement is checked against the
   [statement policy](#statement-policy) and the caller's [permissions](permissions.md), then dispatched to the held
   connection. A statement is recorded as an ordinary query row — `queued`, then `running` once the agent acknowledges
   receipt, then a terminal `done`/`failed` — so you poll and fetch it through the same `GET /api/queries/{id}` and
   `/rows` endpoints as any query, and it appears in the audit history tagged to its session.
3. **Close** — `DELETE /api/sql/sessions/{id}`. The agent drops the connection and frees the compute slot it held.

## Statement delivery and deadlines

A statement's own execution timeout (`timeout_s` on the request, 600s default) is enforced by the agent around
execution, but that alone can't bound a statement whose dispatch frame never arrives — nothing would ever revisit a row
that stays `queued`. Two server-side deadlines close that gap:

- **Ack deadline** (`SQL_STATEMENT_ACK_DEADLINE_S`, default 15s) — the agent acknowledges receipt of a statement before
  doing anything else, flipping the row `queued` → `running`. A row still `queued` past this deadline never reached the
  agent and is failed with `agent did not ack statement`.
- **Timeout + grace** (`SQL_STATEMENT_TIMEOUT_GRACE_S`, default 30s on top of the statement's own `timeout_s`) — a
  `running` statement past this bound should have already been resolved by the agent's own timeout; reaching here means
  its reply is gone too, and it is failed with `statement exceeded timeout`.

A statement that fails this way is **never automatically retried**: if the original frame actually did reach the agent
and only the acknowledgement was lost, retrying would re-run the statement a second time, which can duplicate or
corrupt output for non-idempotent DDL/DML (`CREATE TABLE`, `INSERT`, `MERGE`). The client is expected to resubmit if it
still wants the statement run.

The ack deadline only applies to agents that advertise support for it in their capabilities; an older agent's
statements fall back to the timeout-based bound instead, so a rolling upgrade never fails every in-flight statement the
moment the API is upgraded ahead of its agents.

Terminating a session for any reason — an explicit close, the idle/lifetime reaper, or an agent disconnect — also
resolves any statement still `queued` or `running` on it, rather than leaving the row to be discovered only by a
client's own poll deadline.

## Pinning, lifetime, and failure

A session holds a real connection **and** a memory reservation on its agent for its whole life, so it counts against
that agent's admission budget just like a running query — long-lived sessions can't oversubscribe memory or starve
interactive queries. Because a session's `memory_limit` is fixed when it opens, size it for steady session work rather
than a single huge query.

To keep a crashed client from pinning an agent forever, a background reaper closes sessions that have been **idle** past
`SQL_SESSION_IDLE_TIMEOUT_S` (default 15 minutes) or have run longer than `SQL_SESSION_MAX_LIFETIME_S` (default 4
hours). A session that never finishes opening — the agent's acknowledgement is lost, so its row is stuck `opening` — is
reaped once it is older than `SQL_SESSION_OPENING_DEADLINE_S` (default 2 minutes, and must exceed the open timeout), so
a slot the agent did manage to reserve is never stranded. If the agent's connection drops, DuckHaven fails that agent's
sessions immediately — the held connection is gone and Postgres is the source of truth — and the next statement on the
session returns `409`; the client simply opens a new one. Sessions survive an API restart or failover as long as their
pinned agent stays connected, because every statement is routed by the agent's recorded owner, not by in-memory state.

As a backstop beneath the control-plane reaper, each **agent also holds its own lease** on every session it is running
and self-expires one that goes idle past `SESSION_IDLE_TIMEOUT_S` or outlives `SESSION_MAX_LIFETIME_S` (both agent
settings, deliberately larger than the API's so the reaper stays primary). This is what guarantees a slot is reclaimed
even if a close instruction from the API is lost in flight — the agent that owns the slot frees it on its own clock
rather than holding it until its next reconnect. This is the churn safeguard: a client that opens many sessions and
exits without closing them (repeated `dbt` runs, for instance) can never slowly exhaust an agent's admission budget.

## Statement policy

The one-shot query path enforces a fixed allowlist (data + catalog DDL only). Sessions need more — connection-scoped
`SET`s and `COPY` from staged files — so the session path replaces the allowlist with a **capability-scoped policy**,
still enforced entirely at the API, per statement:

- **Allowed:** `SELECT`/`INSERT`/`UPDATE`/`DELETE`/`MERGE`, `CREATE`/`ALTER`/`DROP`, `DESCRIBE` (read-only relation
  introspection that dbt relies on), `USE`, transaction control, a small safe subset of `SET` (e.g. `timezone`), `COPY`
  **to or from the session's staging prefix only**, and `ATTACH` of the workspace's own managed catalog.
- **Rejected:** `COPY` or `read_parquet`/`read_csv` to a local file, an arbitrary URL, or any object-store path outside
  the staging prefix; arbitrary `INSTALL`/`LOAD`; `ATTACH` of anything else; and any `SET` that could widen the sandbox
  (memory, external access, filesystem allowlists). Anything the parser can't classify is rejected.

This is what keeps a broadened SQL surface from becoming an open one: a session gets a bigger box, not an unbounded box,
and every rejection is counted for monitoring.

## Staging and credentials

Bulk data does **not** flow through the API. A bulk load stages Parquet to the workspace's object storage and then
issues a `COPY` **command** through the session; only the command crosses the API. Each session is given a scoped
`staging_uri` (a unique prefix under its catalog's storage) that the statement policy is pinned to — a `COPY` may only
read or write there.

The API is the credential vendor for a session: it supplies the Polaris connection the agent's session uses, rather than
the agent reading a static secret from its own config. Today that identity is still DuckHaven's shared Polaris service
principal — governance rests on the API's per-statement authorization and the catalog grants, not on the token's
identity. Per-principal Polaris identities are still planned; the staging-write leg, however, no longer needs raw
credentials at all — see below.

## Staging files (presigned URLs)

To get bulk bytes *into* the stage, a client asks the API to presign them rather than handing out storage credentials.
`POST /api/sql/sessions/{session_id}/staging-files` takes a list of file names and returns, per file, a short-lived
presigned **`put_url`** (upload) and **`get_url`** (read) scoped to a key under that session's staging prefix, plus an
`expires_at`. This models a Snowflake internal stage: the broker vends time-boxed, single-key access and bulk bytes flow
directly between the client, the object store, and the agent — never through the control plane.

A load then looks like:

1. `POST …/staging-files` with `{"files": ["orders.parquet"]}` → `put_url` / `get_url`.
2. The client uploads the Parquet with a plain HTTP `PUT` to `put_url` — no storage SDK, no secret.
3. The client runs `INSERT INTO … SELECT * FROM read_parquet('<get_url>')` through the session; the agent reads the
   `get_url` over its httpfs extension with **no staging credential of its own** — all authorization is in the URL
   signature.

Because backend-specific signing lives only in the API, the client and agent treat every backend uniformly as opaque
HTTPS: S3 and the bundled MinIO use SigV4 presigned URLs, Azure ADLS/Blob uses the equivalent SAS URLs. This is why it
works on the bundled MinIO backend, which has **no STS** — a presigned URL is a signature, not a vended session token,
so it grants genuinely narrow, time-boxed access there (narrower than the static credentials Polaris would otherwise
vend). The statement policy admits `read_parquet('https://…')` only when the URL points at the session's own staging
prefix; arbitrary local-FS or external reads are still rejected. Presigned URLs expire, and a request against a
reaped/closed session returns `409` (the client reconnects, exactly as for statement execution).

## Sandboxing

With the allowlist relaxed, the agent is contained at the OS layer instead: the container runs with a **read-only root
filesystem** (only its results volume is writable), dropped Linux capabilities, and `no-new-privileges`, so a stray
local write outside the results volume fails at the kernel. An optional `SANDBOX_DISABLED_FILESYSTEMS` setting can also
disable DuckDB's generic HTTP filesystem to block `COPY … TO 'http://…'` exfiltration; it is **off by default** because
the bundled Polaris and MinIO speak plain HTTP, and is meant for HTTPS-only, egress-restricted deployments. The agent
still needs network egress to Polaris and the storage backend, so restrict it to those at the network layer.

## Observability

Sessions emit their own metrics — sessions opened, sessions closed by reason (client, idle, max-lifetime, open-timeout,
agent-disconnect, agent-self-reap, failed), statements by outcome, statement-policy rejections by rule, an
active-sessions gauge, and a per-agent held-session count — alongside OpenTelemetry spans for open/exec/close that
continue the same trace across the API→agent hop. To watch for a leak, alert when a per-agent held-session count stays
above that agent's share of the active-sessions gauge, or when the `agent-self-reap` close reason is firing at all: the
backstop only fires when a normal close was dropped. See [Monitoring](../operations/monitoring.md).

## Not this

Sessions are for tool connections, not a second interactive UI: the DuckHaven worksheet still uses the one-shot query
path. Sessions also do not add cross-agent transactions — each session is one connection on one agent.
