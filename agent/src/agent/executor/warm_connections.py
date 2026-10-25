"""Connections opened and loaded ahead of the query that will use them.

Opening a query's connection is mostly ``LOAD``. A connection is a new in-memory
database, and DuckDB checks an extension's signature, by hashing the whole file,
every time a database loads it: ~390 ms for a DuckLake workspace's ``httpfs``,
``ducklake`` and ``postgres`` on a 2-vCPU agent (measured on 1.5.6; 2.0.0 is no
faster), against ~3 ms for everything else in an open that attaches nothing. A
spare that has already loaded them takes that off the query's path.

A spare is used once, by one query, like a connection opened on the spot: nothing
is attached, no secret exists and the sandbox is not applied until
``open_and_attach`` takes it, so the pool changes no isolation. It moves the cost
rather than removing it. Refills spend the same CPU in the background, and a burst
wider than the pool opens the rest on the spot, as before.

Refills run pinned to one CPU. DuckDB hashes an extension on one thread per MiB,
so an unpinned refill takes the container's whole CPU quota for a few hundred ms
and the cgroup throttles the query it is meant to speed up: on a 2-vCPU agent
that tripled a DuckLake ATTACH. The hashing threads inherit the pin, so a refill
uses at most one CPU and the query keeps the rest. Lowering the refill's priority
does not help, because the quota is the cgroup's, not the thread's.

Spares wait with ``threads=1``: an idle DuckDB database keeps ``threads - 1``
workers alive, which count against the container's pids limit. The thread count
the database started with is put back when a spare is taken.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import duckdb

logger = logging.getLogger(__name__)

# Workspace shapes (extension sets) kept warm at once. A shape not used for a while
# gives its spares up to a newer one.
_MAX_SHAPES = 4


@dataclass
class Prepared:
    """A connection past the load phase, and the extensions that loaded."""

    conn: duckdb.DuckDBPyConnection
    loaded: frozenset[str]


@dataclass
class _Spare:
    prepared: Prepared
    threads: int


class WarmConnections:
    """Up to ``per_shape`` loaded connections for each recently used shape.

    ``prepare(shape)`` does the load phase for a shape; it runs on one background
    thread, so refills never compete with each other, only with queries.
    """

    def __init__(self, prepare: Callable[[Hashable], Prepared], *, per_shape: int) -> None:
        self._prepare = prepare
        self._per_shape = per_shape
        self._lock = threading.Lock()
        # shape -> spares, least recently used shape first.
        self._spares: OrderedDict[Hashable, list[_Spare]] = OrderedDict()
        self._pending: dict[Hashable, int] = {}
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="dh-warm", initializer=_pin_to_one_cpu
        )

    def take(self, shape: Hashable) -> Prepared | None:
        """A loaded connection for ``shape``, or None if none is ready.

        Either way the shape is topped back up in the background, so after a miss
        the next query of the same shape finds one.
        """
        with self._lock:
            spares = self._spares.setdefault(shape, [])
            self._spares.move_to_end(shape)
            spare = spares.pop() if spares else None
            evicted = self._evict_locked()
            self._refill_locked(shape)
        for old in evicted:
            _close(old)
        if spare is None:
            return None
        try:
            spare.prepared.conn.execute(f"SET threads={spare.threads}")
        except duckdb.Error as exc:
            logger.warning("Discarding a warm connection that failed to wake: %s", exc)
            _close(spare)
            return None
        return spare.prepared

    def close(self) -> None:
        """Close every spare and stop refilling."""
        with self._lock:
            self._closed = True
            spares = [s for shape in self._spares.values() for s in shape]
            self._spares.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for spare in spares:
            _close(spare)

    def _evict_locked(self) -> list[_Spare]:
        evicted: list[_Spare] = []
        while len(self._spares) > _MAX_SHAPES:
            _, spares = self._spares.popitem(last=False)
            evicted.extend(spares)
        return evicted

    def _refill_locked(self, shape: Hashable) -> None:
        if self._closed:
            return
        missing = self._per_shape - len(self._spares[shape]) - self._pending.get(shape, 0)
        for _ in range(missing):
            self._pending[shape] = self._pending.get(shape, 0) + 1
            self._executor.submit(self._fill, shape)

    def _fill(self, shape: Hashable) -> None:
        spare: _Spare | None = None
        try:
            prepared = self._prepare(shape)
            threads = int(prepared.conn.execute("SELECT current_setting('threads')").fetchone()[0])
            prepared.conn.execute("SET threads=1")
            spare = _Spare(prepared, threads)
        except Exception as exc:  # noqa: BLE001 - the next take tries again
            logger.warning("Preparing a warm connection failed: %s", exc)
        with self._lock:
            self._pending[shape] -= 1
            # A shape evicted while this was loading, or a closed pool, keeps nothing.
            keep = spare is not None and not self._closed and shape in self._spares
            if keep:
                self._spares[shape].append(spare)
        if spare is not None and not keep:
            _close(spare)


def _pin_to_one_cpu() -> None:
    """Keep this thread, and the threads it starts, on one of its CPUs."""
    if not hasattr(os, "sched_setaffinity"):  # not Linux
        return
    try:
        os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
    except OSError as exc:
        logger.warning("Could not pin the warm-connection thread: %s", exc)


def _close(spare: _Spare) -> None:
    try:
        spare.prepared.conn.close()
    except Exception:  # noqa: BLE001 - closing a spare is best-effort
        pass
