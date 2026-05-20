import asyncio
from pathlib import Path
from typing import Any

from agent.executor.runner import run_query_sync


async def run_query(
    sql: str,
    result_path: Path,
    memory_limit_gb: float,
    timeout_s: float,
    *,
    backend: dict[str, Any] | None = None,
    storage_credentials: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    uc_endpoint: str | None = None,
) -> dict[str, int]:
    loop = asyncio.get_running_loop()

    def _run() -> dict[str, int]:
        return run_query_sync(
            sql,
            result_path,
            memory_limit_gb,
            backend=backend,
            storage_credentials=storage_credentials,
            workspace_slug=workspace_slug,
            uc_endpoint=uc_endpoint,
        )

    return await asyncio.wait_for(
        loop.run_in_executor(None, _run),
        timeout=timeout_s,
    )
