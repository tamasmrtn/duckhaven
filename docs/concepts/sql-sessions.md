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
   a `session_id` once the agent acknowledges. The session **pins** that agent: every later statement routes to it. When
   no agent is up and [elastic compute](elastic-compute.md) is enabled, the open starts one first — see
   [Cold start](#cold-start).
2. **Run statements** — `POST /api/sql/sessions/{id}/statements`. Each statement is checked against the
   [statement policy](#statement-policy) and the caller's [permissions](permissions.md), then dispatched to the held
   connection. A statement is recorded as an ordinary query row — `queued`, then `running` once the agent acknowledges
   receipt, then a terminal `done`/`failed` — so you poll and fetch it through the same `GET /api/queries/{id}` and
   `/rows` endpoints as any query, and it appears in the audit history tagged to its session.
3. **Close** — `DELETE /api/sql/sessions/{id}`. The agent drops the connection and frees the compute slot it held.

## Cold start

With [elastic compute](elastic-compute.md) enabled, the pool can legitimately be scaled to zero when
a client connects — that is the point of it. Opening a session then has to start an agent and wait,
which sits awkwardly with a synchronous open, so the open call lets the caller choose how that wait
ends.

The session is written **`pending`** — no agent holds it yet — compute is started, and the call
blocks. `SQL_SESSION_WAIT_TIMEOUT_S` (default 45 seconds) is the budget, and it is deliberately one
number for every backend: nothing a client sees depends on whether the deployment provisions Docker
containers or Azure container groups.

Two request fields shape it:

| Field | Meaning |
| --- | --- |
| `wait_timeout_s` | How long to block. Omit for the server default; `0` never blocks. Capped by `SQL_SESSION_MAX_WAIT_TIMEOUT_S` (default 120s). |
| `on_wait_timeout` | `cancel` (default) or `continue` — what happens when the budget runs out. |

And the answers:

| Outcome | Status | Body |
| --- | --- | --- |
| Opened within the budget | `201` | The open session, exactly as before |
| Budget expired, `on_wait_timeout=cancel` | `503` + `Retry-After` | `{"error": "compute_starting"}` |
| Budget expired, `on_wait_timeout=continue` | `202` | The session, still `pending` or `opening` |
| Compute could not be started at all | `503` + `Retry-After` | `{"error": "compute_unavailable"}` |

`cancel` is the default because it is safe for a client that only knows how to open a session: it
gets a plain "not yet, try again" it can retry, and never a session id it would immediately fail to
run statements on. **It abandons the session row, not the compute** — the agent keeps starting, so a
retry a few seconds later lands on warm compute rather than triggering a second cold start.

`continue` is for a client that can poll: it gets the `pending` session back with `202` and follows
`GET /api/sql/sessions/{id}` until the status reads `open`. A `202` on this endpoint is itself the
signal that the server supports the contract; nothing needs to negotiate a version.

!!! note "Client support"
    The DuckHaven clients (`duckhaven-sql-connector`, `dbt-duckhaven`, `dlt-duckhaven`) do not yet
    retry the `503` or poll the `202` themselves. Until they do, a cold start slower than the wait
    budget surfaces as a connection error the caller has to retry. Deployments where compute starts
    in seconds (the Docker backend) are already covered by the default budget.

A session that stays `pending` because compute never arrives at all is failed by the elastic reaper
at `ELASTIC_PROVISIONING_DEADLINE_S` (default 5 minutes), with close reason `provisioning_timeout`.

Naming an idle-terminated elastic agent explicitly starts *that* agent and parks the session for it,
rather than failing — see [Starting for a SQL session](elastic-compute.md#starting-for-a-sql-session).

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

That budget is also what bounds how many sessions one agent can hold at once: roughly its memory budget divided by
`SESSION_RESERVATION_BYTES` (about 14 on a 4 GB agent at the 256 MB default). Opens beyond that **queue** for capacity
rather than failing outright, and an open that has waited `SESSION_QUEUED_TIMEOUT_S` gives up with a clear error instead
of hanging until the control plane's own deadline. If your clients routinely open more sessions at once than an agent
can hold, that is a sizing question — a larger agent, a smaller per-session reservation, or more agents in the pool —
not something a longer timeout fixes.

To keep a crashed client from pinning an agent forever, a background reaper closes sessions that have been **idle** past
`SQL_SESSION_IDLE_TIMEOUT_S` (default 15 minutes) or have run longer than `SQL_SESSION_MAX_LIFETIME_S` (default 4
hours). A session that never finishes opening — the agent's acknowledgement is lost, so its row is stuck `opening` — is
reaped once it is older than `SQL_SESSION_OPENING_DEADLINE_S` (default 2 minutes, and must exceed the open timeout), so
a slot the agent did manage to reserve is never stranded. That deadline runs from when an agent was actually told to
open the session, not from when the client asked, so a session that first waited out a [cold start](#cold-start) still
gets its full budget. Reaping a session this way also reaches an open the agent had **started but not finished** —
one still queued for capacity, or still building its connection — so the reservation it was holding comes back rather
than being lost until the agent restarts. If the agent's connection drops, DuckHaven fails that agent's
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

- **Allowed:** `SELECT`/`INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`/`MERGE`, `CREATE`/`ALTER`/`DROP`, `DESCRIBE` (read-only
  relation introspection that dbt relies on), `USE`, transaction control, a small safe subset of `SET` (e.g.
  `timezone`), `COPY` **to or from the session's staging prefix only**, and `ATTACH` of the workspace's own managed
  catalog. `TRUNCATE` is DuckDB's alias for `DELETE FROM` without a `WHERE` (dbt's seed reset emits it), and is
  authorized as a write against its target — see [SQL support](../reference/sql-support.md).
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

With the allowlist relaxed, the statement policy is no longer the only thing standing between a statement and the
outside world. The agent is contained at three further layers, all on by default.

### The container

The agent container runs with a **read-only root filesystem** (only its results volume and a `/tmp` tmpfs are
writable), dropped Linux capabilities, `no-new-privileges`, a process cap, and a non-root user — so a stray local write
outside the results volume fails at the kernel rather than at a parser.

### Network egress

The agent is attached to an **isolated Docker network with no route off the host**. It can reach the API, Polaris, the
object store, and the trace collector; it cannot reach anything else. This is what contains a statement that tries to
read from or write to an arbitrary address — the second layer the design called for, enforced by the kernel rather than
by SQL parsing.

You can verify it on a running stack:

```sh
docker compose -f deploy/docker-compose.yml exec -T agent python -c "
import duckdb; c = duckdb.connect(); c.execute('LOAD httpfs')
c.execute(\"SELECT content FROM read_text('https://example.com/')\").fetchone()"
# expected: a network/IO error, not a result
```

!!! warning "Some deployments must opt out — and then this layer is gone"
    An agent whose catalogs use **external** storage (`s3`, `adls_gen2`), or that must reach an off-host Polaris or
    collector, needs real outbound access. Those deployments apply `deploy/docker-compose.egress-opt-out.yml`, which
    puts the agent back on the default network. When you do that, **the API statement policy becomes the only remaining
    layer** between a session statement and arbitrary egress. Prefer restricting egress to the specific hosts you need
    (a host firewall, or a Kubernetes `NetworkPolicy` — see [Install](../deployment/install.md)) over removing the
    restriction outright.

### DuckDB configuration

After the agent has set a connection up — extensions loaded, catalogs attached, credentials vended — it **locks
DuckDB's configuration** (`SANDBOX_LOCK_CONFIGURATION`, on by default). A session statement can no longer widen its own
sandbox with `SET`: `disabled_filesystems`, `enable_external_access`, `secret_directory`, `extension_directory`,
`home_directory`, `custom_extension_repository`, and `allow_unsigned_extensions` all become read-only for the life of
the connection, as does the lock itself. A small exception list keeps writable only what the agent needs afterwards —
the per-statement memory/thread slice, the profiler, and the `SET timezone` the statement policy admits.

`SANDBOX_DISABLED_FILESYSTEMS` can additionally disable a whole DuckDB filesystem. It is **off by default** because the
agent reads [staged files](#staging-files-presigned-urls) over presigned HTTP(S) URLs, and disabling `HTTPFileSystem`
would break that. Set it to `HTTPFileSystem` on a deployment that does not use staging. (Contrary to earlier guidance,
it does *not* break the bundled Polaris or MinIO: the Iceberg REST client and the S3 filesystem are independent of the
generic HTTP one.)

## Observability

Sessions emit their own metrics — sessions opened, sessions closed by reason (client, idle, max-lifetime, open-timeout,
agent-disconnect, agent-self-reap, failed), statements by outcome, statement-policy rejections by rule, an
active-sessions gauge, and a per-agent held-session count — alongside OpenTelemetry spans for open/exec/close that
continue the same trace across the API→agent hop. To watch for a leak, alert when a per-agent held-session count stays
above that agent's share of the active-sessions gauge, or when the `agent-self-reap` close reason is firing at all: the
backstop only fires when a normal close was dropped. See [Monitoring](../operations/monitoring.md).

### The audit trail

Metrics tell you *how many* sessions ended badly; the audit trail tells you *which one*. Every session is a row in
Postgres and every statement it runs is an ordinary query row tagged `origin="session"` with its `session_id`, so a
whole `dbt run` is one workload you can read top to bottom instead of a few hundred unattributed history entries. The
**Sessions** screen in the UI renders both — see
[Read the session audit trail](../guides/session-audit.md) for the walkthrough.

Two things the row records that are worth knowing about:

**Why a session ended.** A session's final status (`closed`, `expired`, `failed`) does not say *why*, and "expired"
covers two quite different situations. The row therefore also carries a typed **close reason**:

| Reason | What happened |
| --- | --- |
| `client` | The client called `DELETE /sql/sessions/{id}` — a clean shutdown |
| `idle` | Reaped: no statement for `SQL_SESSION_IDLE_TIMEOUT_S`. Usually a client that crashed or forgot to close |
| `max_lifetime` | Reaped: alive longer than `SQL_SESSION_MAX_LIFETIME_S`, however busy it was |
| `open_timeout` | The agent never confirmed the open, so the session never became usable |
| `compute_timeout` | The open gave up while compute was still starting — see [Cold start](#cold-start) |
| `provisioning_timeout` | The session waited for compute that never arrived at all |
| `agent_disconnect` | The agent holding the connection dropped; everything in flight on it died with it |
| `agent_lease` | The agent self-reaped an orphan it was still holding — the backstop for a lost close |
| `failed` | The agent reported it could not open the connection at all |

An idle reap and an explicit close look identical in an aggregate; here they do not. Sessions that ended before this
field existed have no reason recorded, and the UI reports them as unknown rather than guessing.

**Which tool opened it.** The API reads the request's `User-Agent` when the session opens and stores the product name
and version on the row — `dbt-duckhaven 0.1.0`, `dlt-duckhaven 0.2.0`. It takes the *first* `product/version` token, so
clients are expected to lead their `User-Agent` with the calling application; the connector does so from
`duckhaven-sql-connector` 0.3.0. A session opened by an older connector (or one with no application set) is recorded as
`duckhaven-sql-connector` rather than the workload, so audit rows written before that client fix are attributed to the
connector, not the tool. This is deliberately server-captured rather than client-declared: the client cannot forge it,
cannot forget to set it, and cannot leave a stale value behind after a failure. It is the same idea as PostgreSQL's
`application_name` or Databricks' `client_application` column.

Richer, client-supplied context — a dbt model name, a dlt load id — is **not** yet part of the contract. When it lands
it will be an optional set of labels supplied once at session open, and it will live on the session row rather than
being smuggled through the SQL text.

### Who can see what

**Any member of a workspace can see every session in that workspace, including the SQL each statement ran.** This is
the same visibility the query history has always had, and it is deliberate: sessions run against shared catalogs and
consume shared agent capacity, so "who is running what right now" is workspace-level information. If you need SQL that
one member cannot read, it belongs in a different workspace.

Two capabilities are narrower. Filtering sessions by principal, agent, or time requires the `queries:admin` permission,
matching the query history's audit filters. Force-closing someone else's session — which drops its connection and fails
whatever it had in flight — is likewise admin-only.

Sessions and their statements are kept for as long as the rows are: there is **no** retention sweep for them today, so
plan for the table to grow with your session volume.

## Not this

Sessions are for tool connections, not a second interactive UI: the DuckHaven worksheet still uses the one-shot query
path. Sessions also do not add cross-agent transactions — each session is one connection on one agent.
