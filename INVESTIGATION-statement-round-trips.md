# Investigation: database round trips per SQL-session statement

Working note for the draft PR on `refactor/fewer-db-round-trips`. **Delete this file before the PR merges.** It
records
what was changed, what was measured, and what still has to be found out before the PR is ready.

## Why this PR exists

A session statement (`POST /api/sql/sessions/{id}/statements`) costs the API about 29 ms of CPU on a fast machine
(53 ms with the laptop on battery). The API runs as one uvicorn process with one event loop, so that CPU is a hard
throughput ceiling: about 55–60 statements/s on AC, reached at 4 concurrent clients, after which latency grows
linearly and throughput even falls.

Measured breakdown of that CPU (battery figures, `SELECT 42`, cache off, no first page):

| Cost | Per statement | How it was measured |
|---|---|---|
| Database access | ~37 ms (~70%) | Postgres statement log + in-process microbenchmark: a session cycle ~2.0 ms, a query ~0.8 ms |
| OpenTelemetry | ~6 ms | API run with `OTEL_EXPORTER_OTLP_ENDPOINT=""` |
| `pool_pre_ping` | ~3 ms | Image with `pool_pre_ping: False` |
| sqlglot parsing | ~2% | profiling |

## The session map (before this PR)

A probe image (instrumentation below) recorded every session, transaction, pool checkout and SQL statement during 40
statements. Per statement on `main`:

- **26.9 SQL statements, 7.8 transactions, 7.8 pool checkouts** (each pre-pinged), **4.25 sessions**: one for the HTTP
  request, one per agent frame (`STATEMENT_ACK`, `QUERY_PROGRESS`, `QUERY_DONE`).
- The `queries` row is read 5.5×, the `sql_sessions` row 3.25×, the workspace's catalogs 3.15× (lineage reads
  them twice,
  even for a plain `SELECT`, which never yields an edge), and the catalog access mode once per attached catalog.

## What the PR changes

1. **Lineage is skipped for statements made only of reads** (`is_read_only`, which checks every statement with
   DuckDB's
   parser; `statement_type` only describes the first one). For writes, the catalog context is read once and
   passed into
   `record_execution_lineage` instead of being read twice.
2. **The wait loop does not read back a row it just committed as queued.** `run_statement` enters `completion_waiter`
   before dispatch and passes it to `await_query_done(done=...)`, which then waits first. When the session's
   agent socket
   is in this process (`registry.get(agent_id)`), the poll starts at 0.5 s: its `QUERY_DONE` is handled here
   and sets the
   event, so the poll only backs up a statement the reaper fails. With the socket on another replica the 50 ms poll
   stays.
3. **`STATEMENT_ACK` and `QUERY_PROGRESS` are single conditional `UPDATE`s** instead of a read and a write. Progress
   uses `RETURNING` so the queue-wait histogram is still recorded only on the first progress frame of an interactive
   query (`running_at` was null).
4. **Duplicate reads in the request**: the access mode of every attached catalog in one query
   (`grants.scoped_catalog_ids`), and the session row locked in the read that loads it
   (`db.get(..., with_for_update=True)` instead of `get` then `refresh(with_for_update=True)`; the emitted SQL
   was checked
   to be the same `SELECT … FOR UPDATE` on `sql_sessions` alone).

Docs: `docs/concepts/lineage.md` notes that read-only statements are not parsed for lineage, so
`duckhaven_lineage_extract_failures_total` now counts only statements that write.

Tests: `make test-api` 3194 passed, `pre-commit run --all-files` clean. New tests (each fails on `main` where
it covers
new behaviour): lineage skipped for a read and still recorded for a script that writes after a read; the catalog
context read once; `scoped_catalog_ids` agrees with `is_scoped` (scoped, open, unattached); the wait with an
early waiter
reads once and does not miss a completion signalled before it starts; the wait polls fast only when the agent socket
is remote; the queue-wait histogram is recorded once per query and never for session statements.

## Second round (2026-10-18)

Six more commits on top of the four changes above:

1. **The workspace's catalogs load in one query** (`resolve_workspace_catalogs` joins the storage backend and DuckLake
   login instead of select-in loading them: 3 queries became 1, on every statement and every other caller).
2. **The session cache lookup reads the workspace only when it runs**, not before its own early returns.
3. **Each trace span is exported once.** FastAPI 0.142 configures OTLP export from the `OTEL_*` environment at lifespan
   startup (`fastapi/telemetry/_runtime.py`) and added a second `BatchSpanProcessor` to the provider
   `setup_telemetry` already exports through. Fixed with `telemetry={"auto_configure": False}` on both apps.
4. **No per-ASGI-message spans** (`exclude_spans=["send", "receive"]`): 12 of a statement's 15 spans were `http send`,
   `http receive` and `websocket receive` (one per agent frame). 3 spans remain: the two server spans and
   `exec_statement`.
5. **QUERY_DONE hands the waiting request the terminal row** (`UPDATE … RETURNING` every column, passed through
   `signal_completion`), so the woken request does not read it back. Without the row (reaper, remote socket) it still
   reads.
6. **STATEMENT_ACK and QUERY_PROGRESS run in autocommit** (one round trip instead of BEGIN, UPDATE, COMMIT).

### Results

Probe image, per statement:

| | SQL | Transactions | Spans |
|---|---|---|---|
| `main` | 27.0 | 7.0 | 30 |
| first round (PR as pushed) | 18.0 | 6.0 | 30 |
| this round | **14.0** | **5.0** | **3** |

API CPU and throughput, battery, `SELECT 42`, cache off, medians of 3 alternating rounds (`main`, the PR as first
pushed, this round), no instrumentation:

| | `main` | First round | This round |
|---|---|---|---|
| 1 client: median latency | 74.4 ms | 68.3 ms | **61.3 ms** |
| 1 client: API CPU per statement | 49.5 ms | 43.6 ms | **36.6 ms** |
| 4 clients: statements/s | 25.2 | 30.9 | **37.9** |
| 8 clients: statements/s | 26.2 | — | **38.4** |
| 8 clients: median latency | 303 ms | — | **217 ms** |

Per-change contribution (same campaign, 1 client): items 1–3 took API CPU 43.6 → 39.5 ms, item 5 to 39.4,
item 4 to 37.1, item 6 to 36.6 (and 4-client throughput 33.8 → 37.9). With tracing off entirely the same build
measured 35.3 ms, so tracing now costs 1–2 ms per statement instead of about 6.

### Answers to the first round's questions

1. **The single-client regression does not reproduce.** On battery, in every one of 6 alternating pairs (two
   campaigns), the first round's branch was faster than `main` for one client (66–69 ms against 70–74 ms median,
   41–44 against 48–50 ms API CPU). The probe shows the wait always woke by event, never by the 0.5 s backstop. The
   likely cause of the AC result is CPU frequency and core placement, not the code: this laptop has P-cores 0–3 and
   E-cores 4–7, CPU per statement is *lower* at 4 clients than at 1 (the governor clocks up under load), and in the AC
   run the agent's CPU per statement rose too, though its code did not change. Not verified on AC: the laptop was on
   battery throughout.
2. **Transactions fell by 1, not 2,** because on `main` the pre-wait read was never its own transaction: it ran in the
   one the dispatch's `db.get(Agent)` had opened after the commit. Removing it saved a SQL statement, not a
   transaction. The transaction saved was lineage's.
3. **HA** is still unchecked. Item 5 only applies when the QUERY_DONE is handled in the waiting process; otherwise
   the wait reads the row as before.
4. **AC re-measurement** is still to do; the numbers above are battery only.

## Where the time goes now

Per-statement timeline from the probe (battery, one client, from dispatch): agent reached and acked at ~6 ms; ack
applied ~4 ms; progress applied ~4 ms; QUERY_DONE applied and signalled at ~32 ms; request resumed 0.7 ms later.
**The three frames are applied one after another on the agent's socket loop**, so QUERY_DONE waits behind the ack and
progress frames, which the agent sends within a millisecond or two of each other for a fast statement.

The 14 remaining SQL statements: auth, session lock, membership, catalogs, in-flight statements, scoped access modes,
INSERT, `last_active_at` UPDATE, the dispatch's agent read (its own transaction, after the commit), ack UPDATE,
progress UPDATE, and in QUERY_DONE the query read, session read and UPDATE.

## Next, not in this PR

- **Apply ack and progress frames off the socket loop's critical path** (for example on a per-agent background queue,
  keeping them ordered among themselves). Both UPDATEs are conditional on a non-terminal status, so one applied after
  QUERY_DONE is a no-op. About 8 ms of single-client latency. Needs a decision first: the queue-wait histogram is
  recorded by the first progress frame, so a fast interactive query whose QUERY_DONE overtakes it would go unsampled.
- **Read the agent in the request's transaction** for sessions with the cache off (the cache lookup already loads it
  for the rest), saving the dispatch's separate transaction.
- **Per-session caching** of membership, catalogs and access modes (about 4 SQL per statement), which needs a decision
  on how fast a revoked grant or removed member must take effect.
- **Tracing has no database spans.** `SQLAlchemyInstrumentor` refuses SQLAlchemy 2.1.4 ("only instruments sqlalchemy
  >= 1.0.0, < 2.1.0 … nothing can be instrumented"), so `docs/operations/tracing.md` describes SQL spans that are not
  produced. Re-enabling them would add about 14 spans per statement.
- The cache-on path (the default for clients) was not measured: its lookup adds an in-flight count that repeats
  `name_context`'s query.

## How the measurements were taken

The scripts lived in a session scratchpad and are not in the repo; this is enough to rebuild them.

- **Overlay images.** Build from the stack's own image with the branch's package on top:
  `FROM ghcr.io/tamasmrtn/duckhaven-api:latest` then `COPY api /app/.venv/lib/python3.14/site-packages/api`, with
  `api/src/api` copied in as `api`. Run the stack's API on it with a compose override that sets `services.api.image`,
  added to `COMPOSE_FILE`. Bring the stack back with a plain `docker compose up -d --no-deps api` from `deploy/`.
- **CPU per statement.** Read `usage_usec` from `/sys/fs/cgroup/system.slice/docker-<container
  id>.scope/cpu.stat` before
  and after a 30 s loop of `POST /sql/sessions/{id}/statements` (`SELECT 42 AS answer`, `use_cache: false`,
  `first_page_limit: 0`, `wait_timeout_s: 30`) on one session, and divide by the statement count.
- **Throughput.** K client processes, each with its own SQL session, looping statements for 25 s; report
  statements/s, latency percentiles, and API and agent CPU in cores. The elastic agent has a 2-CPU limit.
- **Query counts from Postgres.** Run `ALTER SYSTEM SET log_statement = 'all'; SELECT pg_reload_conf();` for a
  short run,
  then `ALTER SYSTEM RESET log_statement` and reload again. Postgres logs to stderr, so capture with
  `docker logs deploy-postgres-1 2>&1`. DuckLake's own metadata queries from the agent (`BEGIN TRANSACTION
  ISOLATION LEVEL
  REPEATABLE READ`, `COPY (SELECT "snapshot_id"…`, `DISCARD ALL`) are in the same log and are not the API's.
- **In-process timings.** `docker exec deploy-api-1 python -m api.entrypoint python /tmp/script.py`. The entrypoint
  sets `DATABASE_URL` and `SECRET_KEY` and then execs the script.
- **Profilers that do not work here.** py-spy cannot attach (`ptrace_scope=1` and the container runs as uid 65532); as
  the parent of uvicorn it works only in `--nonblocking` mode, which truncates stacks. cProfile misattributes time
  across SQLAlchemy's greenlet switches and coroutine resumes, so only per-function self time is usable.
- **The timeline probe (second round).** A second overlay-only probe appended to `api/main.py` as
  `from api import _probe; _probe.install()`. It logs SQL (`before_cursor_execute`), pool checkouts, ORM begins and
  every span ended (by patching `BatchSpanProcessor.on_end`, which is how the duplicate processor showed: each span id
  arrived from two processor instances), and times `dispatch_exec_statement`, `handle_statement_ack`,
  `handle_agent_frame`, `signal_completion` and `await_query_done` by wrapping them on their modules. Wrappers must pass
  through extra arguments; one that did not broke `signal_completion` and the wait fell back to the backstop.
- **Do not run the test suite during a benchmark.** It takes several cores for minutes and skews every run it overlaps.
- **The session probe.** Saved as `api/_session_probe.py` in the overlay only, and enabled by appending
  `from api import _session_probe` and `_session_probe.install_pool_probe(engine)` to the overlay's
  `api/db/session.py`.
  It writes JSON lines to `/tmp/sessions.log` inside the container:

```python
import json, time, traceback
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

_LOG = open("/tmp/sessions.log", "a", buffering=1)
_owner, _last_op = {}, {}


def _api_frames():
    return [
        f"{f.filename.split('site-packages/')[1]}:{f.name}"
        for f in traceback.extract_stack()
        if "/site-packages/api/" in f.filename
        and not f.filename.endswith(("_session_probe.py", "db/session.py"))
    ]


def _write(**rec):
    rec["t"] = time.time()
    _LOG.write(json.dumps(rec) + "\n")


_orig_init = AsyncSession.__init__


def _init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    _write(ev="new", sid=id(self.sync_session), site=_api_frames())


AsyncSession.__init__ = _init
for _name in (
    "execute",
    "scalar",
    "scalars",
    "get",
    "commit",
    "flush",
    "refresh",
    "delete",
    "merge",
    "rollback",
):

    def _wrap(orig, op):
        async def method(self, *args, **kwargs):
            caller = _api_frames()[-3:]
            _last_op[id(self.sync_session)] = " > ".join(caller[-1:])
            _write(ev="op", sid=id(self.sync_session), op=op, caller=caller)
            return await orig(self, *args, **kwargs)

        return method

    setattr(AsyncSession, _name, _wrap(getattr(AsyncSession, _name), _name))


@event.listens_for(Session, "after_begin")
def _after_begin(session, transaction, connection):
    _owner[id(connection.connection.dbapi_connection)] = id(session)
    _write(ev="begin", sid=id(session))


def install_pool_probe(engine):
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _sql(conn, cursor, statement, params, context, executemany):
        sid = _owner.get(id(conn.connection.dbapi_connection))
        _write(ev="sql", sid=sid, by=_last_op.get(sid), sql=" ".join(statement.split())[:70])

    @event.listens_for(engine.sync_engine.pool, "checkout")
    def _checkout(dbapi_conn, record, proxy):
        _write(ev="checkout")
```

To count per statement, write a timestamp file in the container before and after N statements and count events of
each kind between the two. Attribute SQL by its text: pooled connections move between sessions, so the
connection-to-session mapping above is only approximate when a request and an agent frame interleave.
