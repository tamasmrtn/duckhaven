"""Background sweep that expires materialized query results (G-D5-a).

Results are Parquet files written per query under `results_dir`; without a
sweep they live for the agent's whole uptime. This deletes files older than
the configured retention window on a fixed interval.
"""

import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def sweep_once(results_dir: Path, retention_hours: float) -> int:
    """Delete result Parquet files older than `retention_hours`.

    Returns the number of files removed. Each unlink is guarded so a file that
    vanished or is being range-read mid-sweep never aborts the pass.
    """
    cutoff = time.time() - retention_hours * 3600
    removed = 0
    for path in results_dir.glob("*.parquet"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Retention sweep could not remove %s: %s", path, exc)
    return removed


async def sweep_loop(results_dir: Path, retention_hours: float, interval_s: float) -> None:
    while True:
        try:
            sweep_once(results_dir, retention_hours)
        except Exception:
            logger.exception("Retention sweep pass failed")
        await asyncio.sleep(interval_s)
