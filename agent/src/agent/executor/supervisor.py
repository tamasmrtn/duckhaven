import asyncio
from pathlib import Path
from typing import Any

import duckdb
from opentelemetry import trace

from agent.executor.runner import run_query_sync
from duckhaven_shared.telemetry import inject_trace_context

_tracer = trace.get_tracer("duckhaven.agent")


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
        )

    def _interrupt() -> None:
        # Called from the event-loop thread; DuckDB's interrupt is thread-safe
        # and stops the in-flight query running on the executor thread.
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
            return await loop.run_in_executor(None, _run, trace_headers)
    except duckdb.InterruptException as exc:
        raise TimeoutError("query exceeded statement timeout") from exc
    except asyncio.CancelledError:
        _interrupt()
        raise
    finally:
        handle.cancel()
