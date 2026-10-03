"""Background sweep that expires materialized query results (G-D5-a).

Results are Parquet files written per query under `results_dir`; without a
sweep they live for the agent's whole uptime. This deletes files older than
the configured retention window on a fixed interval.

A result the control plane's result cache serves rows from is *retained*: a
`<query_id>.retain` sidecar holds the epoch second until which it must stay, and
the sweep leaves it alone until then. The sidecar is a file rather than memory so
retention survives an agent restart. Retained files are bounded by their own byte
budget; past it the ones retained longest ago go first, and the cache finds them
gone on its next lookup and drops the entry -- a miss, never a wrong answer.
"""

import asyncio
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

RETAIN_SUFFIX = ".retain"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _sidecar(results_dir: Path, query_id: str) -> Path | None:
    """The retain sidecar for a query id, or None for anything not a uuid: the id
    comes off the wire and becomes a path."""
    if not _UUID_RE.match(query_id):
        return None
    return results_dir / f"{query_id}{RETAIN_SUFFIX}"


def retain(results_dir: Path, query_id: str, until: float) -> bool:
    """Keep ``query_id``'s result until ``until`` (epoch seconds). False when there
    is no such result to keep."""
    sidecar = _sidecar(results_dir, query_id)
    if sidecar is None or not (results_dir / f"{query_id}.parquet").exists():
        return False
    sidecar.write_text(str(float(until)))
    return True


def release(results_dir: Path, query_id: str) -> None:
    """Stop retaining ``query_id``'s result; the normal window applies again."""
    sidecar = _sidecar(results_dir, query_id)
    if sidecar is not None:
        sidecar.unlink(missing_ok=True)


def _retained_until(sidecar: Path) -> float | None:
    try:
        return float(sidecar.read_text().strip())
    except OSError, ValueError:
        return None


def sweep_once(
    results_dir: Path, retention_hours: float, retained_max_bytes: int | None = None
) -> int:
    """Delete result Parquet files older than `retention_hours`, unless retained.

    Returns the number of files removed. Each unlink is guarded so a file that
    vanished or is being range-read mid-sweep never aborts the pass.
    """
    now = time.time()
    cutoff = now - retention_hours * 3600
    removed = 0
    retained: list[tuple[float, Path, Path, int]] = []
    for path in results_dir.glob("*.parquet"):
        try:
            sidecar = path.with_suffix(RETAIN_SUFFIX)
            until = _retained_until(sidecar) if sidecar.exists() else None
            if until is not None and until > now:
                retained.append((sidecar.stat().st_mtime, path, sidecar, path.stat().st_size))
                continue
            if until is not None or sidecar.exists():
                sidecar.unlink(missing_ok=True)
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Retention sweep could not remove %s: %s", path, exc)

    if retained_max_bytes is not None:
        held = sum(size for *_, size in retained)
        for _, path, sidecar, size in sorted(retained):
            if held <= retained_max_bytes:
                break
            try:
                path.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                held -= size
                removed += 1
            except OSError as exc:
                logger.warning("Retention sweep could not evict %s: %s", path, exc)

    # A sidecar whose result is already gone keeps nothing alive.
    for sidecar in results_dir.glob(f"*{RETAIN_SUFFIX}"):
        if not sidecar.with_suffix(".parquet").exists():
            sidecar.unlink(missing_ok=True)
    return removed


async def sweep_loop(
    results_dir: Path,
    retention_hours: float,
    interval_s: float,
    retained_max_bytes: int | None = None,
) -> None:
    while True:
        try:
            sweep_once(results_dir, retention_hours, retained_max_bytes)
        except Exception:
            logger.exception("Retention sweep pass failed")
        await asyncio.sleep(interval_s)
