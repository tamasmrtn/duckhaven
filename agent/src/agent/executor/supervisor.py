import asyncio
from pathlib import Path
from typing import Any

import duckdb

from agent.executor.runner import run_query_sync


async def run_query(
    sql: str,
    result_path: Path,
    timeout_s: float,
    *,
    memory_bytes: int,
    threads: int,
    backend: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    polaris: dict[str, Any] | None = None,
    default_schema: str | None = None,
    stats_for: dict[str, str] | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    enable_profiling: bool = True,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    # In the `auto` profile the connection is opened+attached before admission
    # (to run EXPLAIN) and handed in here; capture it up front for interrupt.
    conn_box: dict[str, duckdb.DuckDBPyConnection] = {}
    if conn is not None:
        conn_box["conn"] = conn

    def _run() -> dict[str, Any]:
        return run_query_sync(
            sql,
            result_path,
            memory_bytes=memory_bytes,
            threads=threads,
            backend=backend,
            workspace_slug=workspace_slug,
            polaris=polaris,
            default_schema=default_schema,
            stats_for=stats_for,
            conn=conn,
            enable_profiling=enable_profiling,
            on_connect=lambda c: conn_box.__setitem__("conn", c),
        )

    def _interrupt() -> None:
        # Called from the event-loop thread; DuckDB's interrupt is thread-safe
        # and stops the in-flight query running on the executor thread.
        conn = conn_box.get("conn")
        if conn is not None:
            conn.interrupt()

    handle = loop.call_later(timeout_s, _interrupt)
    try:
        return await loop.run_in_executor(None, _run)
    except duckdb.InterruptException as exc:
        raise TimeoutError("query exceeded statement timeout") from exc
    except asyncio.CancelledError:
        _interrupt()
        raise
    finally:
        handle.cancel()
