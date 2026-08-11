import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import duckdb
from opentelemetry import trace

from agent.executor.runner import run_query_sync, run_statement_sync
from agent.metrics.system import effective_cores
from duckhaven_shared.telemetry import inject_trace_context

_tracer = trace.get_tracer("duckhaven.agent")


class StatementAbandoned(TimeoutError):
    """A statement's executor worker was abandoned, not confirmed stopped.

    Raised instead of a plain ``TimeoutError`` when ``conn.interrupt()`` did not
    make the executor future return before the wait bound below gave up on it.
    DuckDB honours interrupts while processing tuples but not while planning —
    a spinning optimizer holds the worker (and the connection) indefinitely,
    exactly like the EXPLAIN-based estimator's own documented spin (see
    ``channel._estimate_under_timeout``), just on the real statement's own
    ``conn.sql(...)`` call this time, which has no such bound today.

    The worker thread is never coming back and may still be running against
    ``conn`` at any point in the future — the caller must never touch that
    connection again (no ``execute``, no ``close``, no reuse for another
    statement). Subclasses ``TimeoutError`` so existing ``except TimeoutError``
    handling still sees a timeout; callers that must avoid reusing the
    connection check for this type specifically.
    """


# Statement/query execution runs on its own pool, isolated from connection
# housekeeping (`session.apply_resize`'s `SET memory_limit`, `_teardown`'s
# `conn.close()`, both on the interpreter's default pool). Without this, a
# worker abandoned to a spinning planner (see `StatementAbandoned`) would
# eventually exhaust the default pool too, the same way an unbounded EXPLAIN
# would have exhausted query execution before `_estimate_pool` split it off.
_execution_pool: ThreadPoolExecutor | None = None


def _execution_pool_get() -> ThreadPoolExecutor:
    global _execution_pool
    if _execution_pool is None:
        _execution_pool = ThreadPoolExecutor(
            max_workers=max(2, effective_cores()), thread_name_prefix="dh-exec"
        )
    return _execution_pool


async def run_query(
    sql: str,
    result_path: Path,
    timeout_s: float,
    *,
    memory_bytes: int,
    threads: int,
    catalogs: list[dict[str, Any]] | None = None,
    active_catalog: str | None = None,
    polaris: dict[str, Any] | None = None,
    stats_for: dict[str, str] | None = None,
    health_for: dict[str, Any] | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    enable_profiling: bool = True,
    disabled_filesystems: str | None = None,
    lock_config: bool = False,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    # In the `auto` profile the connection is opened+attached before admission
    # (to run EXPLAIN) and handed in here; capture it up front for interrupt.
    conn_box: dict[str, duckdb.DuckDBPyConnection] = {}
    if conn is not None:
        conn_box["conn"] = conn

    def _run(trace_headers: dict[str, str] | None) -> dict[str, Any]:
        return run_query_sync(
            sql,
            result_path,
            memory_bytes=memory_bytes,
            threads=threads,
            catalogs=catalogs,
            active_catalog=active_catalog,
            polaris=polaris,
            stats_for=stats_for,
            health_for=health_for,
            conn=conn,
            enable_profiling=enable_profiling,
            on_connect=lambda c: conn_box.__setitem__("conn", c),
            trace_headers=trace_headers,
            disabled_filesystems=disabled_filesystems,
            lock_config=lock_config,
        )

    def _interrupt() -> None:
        # Called from the event-loop thread; DuckDB's interrupt is thread-safe
        # and stops the in-flight query running on the executor thread. Best
        # effort: it does nothing while the worker is still planning, which is
        # exactly the case `wait_for` below exists to bound.
        conn = conn_box.get("conn")
        if conn is not None:
            conn.interrupt()

    handle = loop.call_later(timeout_s, _interrupt)
    try:
        # Manual span so per-query DuckDB execution time is visible in the
        # trace; the query id is the result file's stem.
        with _tracer.start_as_current_span(
            "duckdb.execute",
            attributes={
                "db.system.name": "duckdb",
                "duckhaven.query_id": result_path.stem,
            },
        ):
            # Captured here (event-loop thread, inside the span) and passed
            # in: run_in_executor does not propagate contextvars to the
            # worker thread, so trace.get_current_span() would see nothing
            # if called from inside _run.
            trace_headers = inject_trace_context()
            # `wait_for`, not a bare await: the executor future is the only
            # thing that can be abandoned, because the thread behind it cannot
            # be stopped once it is inside a spinning DuckDB call that ignores
            # `interrupt()`. Without this bound, that one query holds this
            # coroutine (and everything serialized behind it) forever.
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(_execution_pool_get(), _run, trace_headers), timeout_s
                )
            except TimeoutError as exc:
                raise StatementAbandoned("query exceeded statement timeout") from exc
    except duckdb.InterruptException as exc:
        raise TimeoutError("query exceeded statement timeout") from exc
    except asyncio.CancelledError:
        _interrupt()
        raise
    finally:
        handle.cancel()


async def run_statement(
    sql: str,
    result_path: Path,
    timeout_s: float,
    *,
    conn: duckdb.DuckDBPyConnection,
    memory_bytes: int,
    threads: int,
    enable_profiling: bool = True,
    watermarks: dict[str, int] | None = None,
    admission_wait_ms: float = 0.0,
) -> dict[str, Any]:
    """Run one statement on a held SQL-session connection with a wall-clock
    timeout (and cancellation) enforced via DuckDB's thread-safe `interrupt()`,
    backstopped by `wait_for` for when interrupt doesn't help (see
    `StatementAbandoned`).

    Mirrors `run_query`, but the connection is owned by the session and is never
    closed here; `run_statement_sync` runs the materialize-or-execute path on it.
    A `StatementAbandoned` here means the caller must tear the session down
    rather than run another statement on it — see `channel._handle_exec_statement`.
    """
    loop = asyncio.get_running_loop()

    def _run() -> dict[str, Any]:
        return run_statement_sync(
            sql,
            result_path,
            conn=conn,
            memory_bytes=memory_bytes,
            threads=threads,
            enable_profiling=enable_profiling,
            watermarks=watermarks,
            admission_wait_ms=admission_wait_ms,
        )

    def _interrupt() -> None:
        conn.interrupt()

    handle = loop.call_later(timeout_s, _interrupt)
    try:
        with _tracer.start_as_current_span(
            "duckdb.execute_statement",
            attributes={
                "db.system.name": "duckdb",
                "duckhaven.statement_id": result_path.stem,
            },
        ):
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(_execution_pool_get(), _run), timeout_s
                )
            except TimeoutError as exc:
                raise StatementAbandoned("statement exceeded timeout") from exc
    except duckdb.InterruptException as exc:
        raise TimeoutError("statement exceeded timeout") from exc
    except asyncio.CancelledError:
        _interrupt()
        raise
    finally:
        handle.cancel()
