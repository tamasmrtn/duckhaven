# SQL sessions

A **SQL session** is a persistent database connection that DuckHaven holds open on one
[agent](agents.md) on your behalf, so a client can run many statements with connection-scoped state — temporary
relations, `USE catalog.schema`, `SET`, and multi-statement transactions — instead of the one-shot
[query execution](query-execution.md) path where every request is independent.

Sessions exist for tools that expect a warehouse connection: **dbt** and **dlt** (and, later, a small Python
connector they share). Like Databricks, a client never talks to a compute node directly — it opens a session through the
DuckHaven API, and the API brokers each statement to the agent over the agent's own outbound WebSocket. No inbound agent
port is ever opened.

!!! note "Off by default"
    The session surface is disabled unless an operator sets `SQL_SESSIONS_ENABLED=true`. Enable it **only after
    deploying the hardened agent** (see [Sandboxing](#sandboxing) below): turning sessions on also enables the broader
    statement policy, so the container hardening must be in place first.

## Lifecycle

1. **Open** — `POST /api/workspaces/{ws}/sql/sessions`. The API picks the agent (an explicit `agent_id`, or an
   auto-picked compatible one), tells it to open and attach a DuckDB connection to the workspace's catalogs, and returns
   a `session_id` once the agent acknowledges. The session **pins** that agent: every later statement routes to it.
2. **Run statements** — `POST /api/sql/sessions/{id}/statements`. Each statement is checked against the
   [statement policy](#statement-policy) and the caller's [permissions](permissions.md), then executed on the held
   connection. A statement is recorded as an ordinary query row, so you poll and fetch it through the same
   `GET /api/queries/{id}` and `/rows` endpoints as any query — and it appears in the audit history tagged to its
   session.
3. **Close** — `DELETE /api/sql/sessions/{id}`. The agent drops the connection and frees the compute slot it held.

## Pinning, lifetime, and failure

A session holds a real connection **and** a memory reservation on its agent for its whole life, so it counts against
that agent's admission budget just like a running query — long-lived sessions can't oversubscribe memory or starve
interactive queries. Because a session's `memory_limit` is fixed when it opens, size it for steady dbt/dlt work rather
than a single huge query.

To keep a crashed client from pinning an agent forever, a background reaper closes sessions that have been **idle** past
`SQL_SESSION_IDLE_TIMEOUT_S` (default 15 minutes) or have run longer than `SQL_SESSION_MAX_LIFETIME_S` (default 4
hours). If the agent's connection drops, DuckHaven fails that agent's sessions immediately — the held connection is gone
and Postgres is the source of truth — and the next statement on the session returns `409`; the client simply opens a new
one. Sessions survive an API restart or failover as long as their pinned agent stays connected, because every statement
is routed by the agent's recorded owner, not by in-memory state.

## Statement policy

The one-shot query path enforces a fixed allowlist (data + catalog DDL only). Sessions need more — dbt runs `SET`, dlt
issues `COPY` from staged files — so the session path replaces the allowlist with a **capability-scoped policy** that is
still enforced entirely at the API, per statement:

- **Allowed:** `SELECT`/`INSERT`/`UPDATE`/`DELETE`/`MERGE`, `CREATE`/`ALTER`/`DROP`, `USE`, transaction control, a small
  safe subset of `SET` (e.g. `timezone`), `COPY` **to or from the session's staging prefix only**, and `ATTACH` of the
  workspace's own managed catalog.
- **Rejected:** `COPY` or `read_parquet`/`read_csv` to a local file, an arbitrary URL, or any object-store path outside
  the staging prefix; arbitrary `INSTALL`/`LOAD`; `ATTACH` of anything else; and any `SET` that could widen the sandbox
  (memory, external access, filesystem allowlists). Anything the parser can't classify is rejected.

This is what keeps a broadened SQL surface from becoming an open one: dbt/dlt get a bigger box, not an unbounded box,
and every rejection is counted for monitoring.

## Staging and credentials

Bulk data does **not** flow through the API. Following the Databricks/MotherDuck pattern, a dlt load stages Parquet to
the workspace's object storage and then issues a `COPY` **command** through the session; only the command crosses the
API. Each session is given a scoped `staging_uri` (a unique prefix under its catalog's storage) that the statement
policy is pinned to — a `COPY` may only read or write there.

The API is the credential vendor for a session: it supplies the Polaris connection the agent's session uses, rather than
the agent reading a static secret from its own config. Today that identity is still DuckHaven's shared Polaris service
principal — governance rests on the API's per-statement authorization and the catalog grants, not on the token's
identity. Per-principal Polaris identities and short-lived STS credentials for external S3 staging are planned; on the
bundled MinIO backend (which has no STS) staging is scoped by the unique prefix plus the policy that confines `COPY` to
it.

## Sandboxing

With the allowlist relaxed, the agent is contained at the OS layer instead: the container runs with a **read-only root
filesystem** (only its results volume is writable), dropped Linux capabilities, and `no-new-privileges`, so a stray
local write outside the results volume fails at the kernel. An optional `SANDBOX_DISABLED_FILESYSTEMS` setting can also
disable DuckDB's generic HTTP filesystem to block `COPY … TO 'http://…'` exfiltration; it is **off by default** because
the bundled Polaris and MinIO speak plain HTTP, and is meant for HTTPS-only, egress-restricted deployments. The agent
still needs network egress to Polaris and the storage backend, so restrict it to those at the network layer.

## Observability

Sessions emit their own metrics — sessions opened, sessions closed by reason (client, idle, max-lifetime,
agent-disconnect, failed), statements by outcome, statement-policy rejections by rule, an active-sessions gauge, and a
per-agent held-session count — alongside OpenTelemetry spans for open/exec/close that continue the same trace across the
API→agent hop. See [Monitoring](../operations/monitoring.md).

## Not this

Sessions are for tool connections, not a second interactive UI: the DuckHaven worksheet still uses the one-shot query
path. Sessions also do not add cross-agent transactions — each session is one connection on one agent.
