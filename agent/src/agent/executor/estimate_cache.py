"""Agent-global cache of EXPLAIN-based memory estimates.

An estimate is a pure function of the SQL and the catalogs it binds against, so
it does not need recomputing per session. That matters for more than the ~12-195
ms an ``EXPLAIN`` costs: DuckDB's planner can *spin* on a complex query planned
against a freshly attached Iceberg catalog (see
``channel._estimate_under_timeout``), and every plan avoided is an exposure
avoided. A fresh-session-per-query workload used to re-plan every statement,
which is exactly where both observed spins happened.

Keyed on the catalog set as well as the SQL, so two workspaces are never served
each other's estimate even for byte-identical text. Entries expire on a TTL,
because table statistics move underneath us as data lands and compaction runs —
a stale estimate only mis-sizes a reservation, but it should not be permanent.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class EstimateKey:
    """What an estimate actually depends on.

    ``catalogs`` is a frozenset of the slugs attached to the connection: the same
    text against a different catalog is a different query, and mixing the two
    across workspaces would be a real isolation break rather than a cache miss.
    """

    catalogs: frozenset[str]
    schema: str
    sql: str


class EstimateCache:
    """A small TTL + LRU cache. Not thread-safe: it is touched only from the
    event loop, like the rest of the sizing path."""

    def __init__(self, *, ttl_s: float, max_entries: int) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[EstimateKey, tuple[int | None, float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: EstimateKey) -> tuple[bool, int | None]:
        """``(hit, estimate)``. The estimate may legitimately be ``None`` — that
        means "known to be unestimable", which is worth caching too, so an
        unestimable statement is not re-planned on every execution."""
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return False, None
        estimate, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._entries[key]
            self.misses += 1
            return False, None
        self._entries.move_to_end(key)
        self.hits += 1
        return True, estimate

    def put(self, key: EstimateKey, estimate: int | None) -> None:
        self._entries[key] = (estimate, time.monotonic() + self._ttl_s)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate_all(self) -> None:
        """Drop everything. Called when a statement runs that could change what a
        later plan binds to — cheaper, and far easier to reason about, than
        tracking which tables each cached plan touched."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
