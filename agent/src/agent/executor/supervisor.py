import asyncio
from pathlib import Path
from typing import Any

import duckdb

from agent.executor.runner import run_query_sync


async def run_query(
    sql: str,
    result_path: Path,
    memory_limit_gb: float,
    timeout_s: float,
    *,
    backend: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    polaris: dict[str, Any] | None = None,
    stats_for: dict[str, str] | None = None,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    conn_box: dict[str, duckdb.DuckDBPyConnection] = {}

    def _run() -> dict[str, Any]:
        return run_query_sync(
            sql,
            result_path,
            memory_limit_gb,
            backend=backend,
            workspace_slug=workspace_slug,
            polaris=polaris,
            stats_for=stats_for,
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
