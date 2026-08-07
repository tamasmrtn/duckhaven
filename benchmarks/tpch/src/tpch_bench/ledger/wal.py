"""Append-only, durable event log.

Every result is appended here — before any DuckDB write — so this is what
survives a hard crash or an accidental early `terraform destroy` mid-scenario:
even if the ledger's last upsert is stale, the WAL has everything since. For
the `concurrent` scenario, each worker enqueues to a single writer coroutine
that owns this file, rather than writing directly (JSONL append is not safely
concurrent across processes, only through one owned file handle).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class WalWriter:
    """One JSONL file per run. Flushed and fsynced per line, so a crash loses
    at most the write currently in flight, never anything already appended."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, table: str, row: dict[str, Any]) -> None:
        line = json.dumps({"table": table, "row": row}, default=str)
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> WalWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_events(path: str | Path) -> list[dict[str, Any]]:
    """Every event in a WAL file, in append order.

    A missing file returns an empty list rather than raising, so a run that
    crashed before its first write is still replayable (as a no-op).
    """
    path = Path(path)
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
