---
type: concept
---

# SQL sessions

A SQL session is a database connection that DuckHaven holds open on one agent, so a tool can run many statements that
share state.

A worksheet query is independent of every other query: it opens a fresh connection, runs, and closes it. dbt, dlt and
other drivers expect a warehouse connection instead: open it, run a sequence of statements that rely on each other,
close it. A session gives them that. Statements in a session share temporary tables, the current catalog and schema
(`USE`), a few `SET`s and transactions. The tool still never talks to the agent: it sends each statement to the
DuckHaven API, which checks it and passes it to the agent over the agent's own outbound connection.

!!! info "Availability"
    Off until an operator sets `SQL_SESSIONS_ENABLED=true` and runs `docker compose up -d`. Turn it on only with the
    hardened agent described under [Sandboxing](#sandboxing), which the bundled agent is, because sessions accept more
    statements than queries do. Any workspace member can open a session.

To use sessions from a tool, see [Connect dbt](connect-dbt.md) and [Load data with dlt](load-data-with-dlt.md). The
endpoints and fields are in the [SQL sessions API](../reference/sql-sessions-api.md).

## Lifecycle

1. **Open.** The API picks an agent, or uses the one the client names, and tells it to open a connection with the
   session's active catalog attached. Other catalogs of the workspace are attached the first time a statement names
   one, so opening a session costs the same however many catalogs the workspace has. The session **pins** that agent:
   every later statement goes to it.
2. **Run statements.** Each statement is checked against the [statement policy](#statement-policy) and the caller's
   [access](../access/how-access-works.md), then run on the held connection. It is recorded as an ordinary query, so it
   appears in History, tagged with its session, and its rows are read like any query's. The call waits for a short
   statement to finish, so most statements cost one request.
3. **Close.** The client closes the session; the agent drops the connection and frees the memory it held.

## Cold start

With [elastic compute](../compute/elastic-compute.md), the pool can be scaled to zero when a tool connects. Opening a
session then starts an agent and waits for it, for up to `SQL_SESSION_WAIT_TIMEOUT_S` (45 seconds by default) on every
compute backend. If compute is not up by then, the client chooses what happens: it gets either "retry later"
(**503** `compute_starting`) or the session back still `pending`, to poll until it opens. Either way the agent keeps
starting, so a retry lands on warm compute instead of starting a second one.

`duckhaven-sql-connector`, and so `dbt-duckhaven` and `dlt-duckhaven`, waits out a cold start of up to five minutes on
its own. A session that waits for compute that never arrives is failed after `ELASTIC_PROVISIONING_DEADLINE_S`.

Naming a stopped elastic agent starts that agent for the session instead of failing; see
[Starting a terminated agent by naming it](../compute/elastic-compute.md#starting-a-terminated-agent-by-naming-it).

## Pinning, lifetime, and failure

A session holds a connection **and** memory on its agent for its whole life, so it counts against the agent's capacity
like a running query. Long-lived sessions cannot oversubscribe the agent or starve worksheet queries.

Under the default `auto` concurrency profile, a session holds a small idle baseline (`SESSION_BASELINE_BYTES`) between
statements. Each statement grows the reservation to the size its plan needs, runs, and shrinks back, so one heavy
statement does not keep its memory for the rest of the session. If the agent is busy, a statement that can get at least
half of what it asked for (`STATEMENT_ADMISSION_FLOOR_FRACTION`) runs at that size, possibly spilling to disk; below
that it waits up to `STATEMENT_ADMISSION_WAIT_S` for memory (see
[When the agent is full](query-execution.md#when-the-agent-is-full)). A session also keeps the spare memory the agent
lends it between statements, so a run that reads the same tables repeatedly, such as `dbt run`, does not fetch them
again. That memory is taken back the moment another query needs it.

How many sessions an agent holds depends on the baseline: roughly the agent's memory budget divided by
`SESSION_BASELINE_BYTES`, about 56 on a 4 GB agent. Opens beyond that **queue** for capacity, and give up after
`SESSION_QUEUED_TIMEOUT_S` with an error. If tools routinely open more sessions than an agent holds, add or enlarge
agents; a longer timeout does not help.

!!! note "Static concurrency profiles"
    Under a static profile (`single`, `equal_2`, …) a session takes a whole slot for its life and does not grow or
    shrink. `single` gives one session the entire agent, so no other query runs until it closes.

Sessions end on their own when the client does not end them:

- **Idle or too old.** A session with no statement for `SQL_SESSION_IDLE_TIMEOUT_S` (15 minutes), or older than
  `SQL_SESSION_MAX_LIFETIME_S` (4 hours), is closed by the API. The agent also expires sessions on its own, later, in
  case the API's close is lost, so a crashed or forgotten client never holds memory for good.
- **Never opened.** A session the agent never confirmed is failed after `SQL_SESSION_OPENING_DEADLINE_S`, counted from
  when an agent was told to open it, so a cold start does not eat into it.
- **Agent gone.** If the agent disconnects, its sessions fail at once, along with any statement in flight. The next
  statement answers **409** `session_not_open`; the client opens a new session.

Sessions survive an API restart or failover as long as their agent stays connected.

A statement that never reaches the agent, or whose result is lost, is failed rather than left `queued` or `running`. It
is never retried automatically: if it did reach the agent, running it again could duplicate an `INSERT` or a `MERGE`.
The client resubmits it if it still wants it run.

## Statement policy

Sessions accept more than the query path, because tools need a few session-level commands, so the API checks each
session statement against its own policy. Beyond everything a query may run, a session may:

- set the catalog and schema with `USE`, and set `timezone`, `search_path` and `schema`;
- open and end transactions;
- `COPY` to or from, and read files under, the session's own **staging prefix**, and nowhere else;
- `ATTACH` a catalog the workspace binds.

It still refuses anything that could widen the sandbox: other `SET`s, `INSTALL` and `LOAD`, attaching anything else,
files and URLs outside the staging prefix, and the [functions rejected
everywhere](../reference/sql-support.md#rejected-functions). Anything the parser cannot classify is refused. The full
list, checked by a test against the code, is in [SQL support](../reference/sql-support.md#allowed-statements).

### Grants follow the session's context

In a workspace with a [scoped catalog](../access/scoped-access.md), each statement's tables are checked against your
grants. An unqualified name such as `orders` is checked where the session will resolve it: in the catalog and schema the
session opened in, until a statement moves it. After `USE sales.raw`, `SELECT * FROM orders` is checked as
`sales.raw.orders`, including later in the same script.

Sometimes DuckHaven cannot know where an unqualified name will resolve:

- after `SET search_path`, which lets one name resolve in several schemas, until a `USE` or `SET schema` replaces it;
- while a statement that can move the context is still queued or running;
- after a one-part `USE x` or a `SET schema` in the same script, which only the engine can resolve;
- after a context change the agent did not report.

Then a statement with an unqualified table name is refused with an explanation instead of checked against a guess.
Fully qualified names (`catalog.schema.table`) always work, and the next `USE catalog.schema` restores the normal check.
Workspaces without a scoped catalog are unaffected.

## Staging files

Bulk data does not flow through the API. A load stages Parquet files in the catalog's object storage, then runs a
`COPY` or `INSERT … SELECT` through the session; only the command crosses the API.

Each session has its own staging prefix under its catalog's storage. To upload, the client asks the API for presigned
URLs, one pair per file: a `put_url` to upload with a plain HTTP `PUT`, and a `get_url` the agent reads. Neither the
client nor the agent holds a storage credential; each URL is signed by the API, works for one object, and expires.
This works the same on S3, Azure and the bundled object store. The upload goes from the client straight to object
storage, so the client must reach the address in the URL; on the bundled stack that address is
`S3_ENDPOINT_PUBLIC`. The steps are in the [SQL sessions API](../reference/sql-sessions-api.md#staging-files).

The agent's storage access for the catalog itself comes from the API too: DuckHaven's shared Polaris principal, with
every statement checked by the API against the caller's own access.

## Sandboxing

Because sessions accept more SQL, the statement policy is not the only boundary. Three more layers contain an agent, all
on by default:

- **The container** runs with a read-only root filesystem (only its results volume and `/tmp` are writable), dropped
  Linux capabilities, `no-new-privileges`, a process limit and a non-root user.
- **Network egress.** The agent sits on an isolated Docker network with no route off the host. It reaches the API,
  Polaris, the object store and the trace collector, and nothing else.
- **DuckDB configuration.** After setting up a connection, the agent locks DuckDB's configuration
  (`SANDBOX_LOCK_CONFIGURATION`), so a statement cannot change filesystem access, extension loading or secret storage.
  The agent reads the lock back and reports it as `verified`, `failed` or `disabled`; an agent whose lock `failed` is
  refused sessions with `agent_sandbox_unverified`, and the API never picks it for one. See
  [Runtimes](../compute/runtimes.md).

To check the egress layer on a running stack, try to reach the internet from the agent:

```bash
docker compose exec -T agent python -c "
import duckdb; c = duckdb.connect(); c.execute('LOAD httpfs')
c.execute(\"SELECT content FROM read_text('https://example.com/')\").fetchone()"
```

It should fail with a network error, not return a page.

!!! warning "Opting out of egress removes a layer"
    An agent whose catalogs use external storage (`s3`, `adls_gen2`), or that must reach an off-host Polaris or
    collector, needs outbound access, and runs with `deploy/docker-compose.egress-opt-out.yml`. The statement policy is
    then the only layer between a session and arbitrary addresses. Prefer limiting egress to the hosts you need, with a
    host firewall or a Kubernetes `NetworkPolicy` (see [Install](../deployment/install.md)), over removing it.

`SANDBOX_DISABLED_FILESYSTEMS` can also turn off a DuckDB filesystem. It is off by default, because the agent reads
staged files over presigned HTTPS URLs and disabling `HTTPFileSystem` would break that. Set it to `HTTPFileSystem` on a
deployment that does not stage files; the Iceberg catalog and the object store do not use that filesystem.

## Audit and visibility

Every session is recorded, and every statement it runs is a query tagged with its session, so a `dbt run` reads as one
workload instead of hundreds of history entries. The **Connections** screen shows them; see
[Read the session audit trail](session-audit.md).

- **The client** is read from the `User-Agent` when the session opens, for example `dbt-duckhaven 0.5.0`, and each
  statement carries it. The client cannot forge or forget it.
- **Tags**, such as the dbt model a statement belongs to, are sent by the client per statement. See
  [Client and tags](../reference/rest-api.md#client-and-tags).
- **Why it ended** is recorded as a close reason, such as `client`, `idle` or `agent_disconnect`.

Every workspace member can see every session in the workspace, including each statement's SQL, as with query history:
sessions share the workspace's catalogs and agents. Put SQL that a member must not read in a different workspace. Only
the member who opened a session can run statements in it or stage files for it, because each statement runs, is checked
and is recorded as that member. Filtering sessions by person, agent or time, and force-closing someone else's session,
need the `queries:admin` permission. Sessions and their statements are kept with no retention limit, so the tables grow
with use.

## The result cache

A read in a session can be answered from the [result cache](result-cache.md), but only outside a transaction and only
until the session creates something of its own: a temporary table, view or macro, a `search_path`, or an extra
`ATTACH`. See [In SQL sessions](result-cache.md#in-sql-sessions). Open the session with `use_cache: false` to turn the
cache off for it.

## Not this

Sessions are for tools. The worksheet runs queries, not sessions. A session is one connection on one agent, so it does
not give transactions across agents.

## Related

- [SQL sessions API](../reference/sql-sessions-api.md) — endpoints, fields and close reasons.
- [Connect dbt](connect-dbt.md) and [Load data with dlt](load-data-with-dlt.md) — sessions from a tool.
- [Query execution](query-execution.md) — the query path sessions sit beside.
- [Configuration](../reference/configuration.md#sql-sessions) — every setting named here.
