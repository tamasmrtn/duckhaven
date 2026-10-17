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

## Results so far

Round trips (probe image, 40 statements):

| | SQL | Transactions = pool checkouts | Sessions |
|---|---|---|---|
| `main` | 26.9 | 7.8 | 4.25 |
| this branch | **19.4** | **6.5** | 4.25 |

API CPU and throughput (cgroup `cpu.stat` deltas, no instrumentation; `main` and the branch alternated):

| Condition | `main` | Branch |
|---|---|---|
| Battery, 1 client: API CPU per statement | 52.5 ms | 48.6 ms |
| AC, 1 client: API CPU per statement | 29.0 ms | **31.5 ms** |
| AC, 1 client: median latency | 48.7 ms | **59 ms** |
| AC, 4 clients: statements/s | 58–60 | **67–68** |
| AC, 8 clients: statements/s | 55 | **65** |
| AC, 8 clients: median latency | 137 ms | **116 ms** |

The laptop switched from battery to AC during the session; only same-condition pairs are comparable.

## What still needs to be investigated before the PR is ready

1. **The single-client regression on AC (+10 ms median latency, +2 ms API CPU, and agent CPU per statement up from
   about 19 to 22 ms) is unexplained.** Under load the branch is clearly better, but one client should not get slower.
   Ruled out so far: the `FOR UPDATE` query (identical SQL, no joins) and `is_read_only` (0.002 ms per call).
   Next step
   was a bisection: images of `main` plus exactly one change group, each measured for single-client latency and
   CPU. The
   groups by hunk of `git diff -U2 main`:

   | Group | Hunks |
   |---|---|
   | wait | `routers/sql_sessions.py` 0, 1, 4; `services/sql_sessions/service.py` 1, 2, 3 |
   | lock | `routers/sql_sessions.py` 2, 3 |
   | ack/progress | `services/sql_sessions/service.py` 0; `services/query.py` 0 |
   | lineage | `services/query.py` 1; `services/lineage/ingest.py` 0, 1, 2 |
   | batch | `services/grants.py` 0, 1 |

   Prime suspects:
   - **The wait change.** Does `QUERY_DONE` always arrive and set the event before the 0.5 s backstop? A median shift
     with an unchanged p90 suggests a different wake-up timing, not missed events. Log, per statement, whether
     the wait
     woke by event or by timeout.
   - **Agent frame ordering.** The three frames are applied in sequence on the agent's websocket loop, and completion
     is signalled only after `QUERY_DONE` commits. Time how long each frame handler takes on `main` and on the branch.
2. **Transactions fell by 1.3, not the expected 2.** The lineage commit and the pre-wait read are each one
   transaction.
   Confirm with the probe which one still happens.
3. **The wait change under high availability.** The fast poll is kept when the agent's socket is on another replica.
   Run the HA compose file (`deploy/docker-compose.ha.yml`) and check that statements on a remote-socket agent are not
   slower, and that an agent reconnecting to another replica mid-statement costs at most the 0.5 s backstop.
4. **Repeat the measurements on AC**, with at least 3 alternating runs, before quoting numbers in the PR description.
   The battery and AC figures above disagree on direction for one client.
5. **Not in this PR, but next:** per-session caching of membership, catalogs and access modes (about 6 SQL per
   statement). It needs a decision on how fast a revoked grant or removed member must take effect. Also: OpenTelemetry
   sampling (about 6 ms per statement), and the JSON first page's remaining ~26 ms of API CPU, likely a new
   `httpx.AsyncClient` per agent fetch in `proxy_rows`.

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
        if "/site-packages/api/" in f.filename and not f.filename.endswith(("_session_probe.py", "db/session.py"))
    ]


def _write(**rec):
    rec["t"] = time.time()
    _LOG.write(json.dumps(rec) + "\n")


_orig_init = AsyncSession.__init__


def _init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    _write(ev="new", sid=id(self.sync_session), site=_api_frames())


AsyncSession.__init__ = _init
for _name in ("execute", "scalar", "scalars", "get", "commit", "flush", "refresh", "delete", "merge", "rollback"):
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
