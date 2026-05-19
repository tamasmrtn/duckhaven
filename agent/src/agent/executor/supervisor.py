import asyncio
from pathlib import Path

from agent.executor.runner import run_query_sync


async def run_query(
    sql: str,
    result_path: Path,
    memory_limit_gb: float,
    timeout_s: float,
) -> dict[str, int]:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, run_query_sync, sql, result_path, memory_limit_gb),
        timeout=timeout_s,
    )
