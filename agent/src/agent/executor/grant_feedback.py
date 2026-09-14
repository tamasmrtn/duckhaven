"""What a statement shape *actually* used, remembered to size the next one.

The ``auto`` profile sizes a statement from ``estimator.estimate_memory_bytes``,
which sums ``cardinality x row_width`` over every blocking operator in the plan.
That assumes every one of them holds its full estimated cardinality at full row
width simultaneously, so it overshoots badly: measured against real peaks on a
SF10 TPC-H run, the estimate was **5-11x** the memory the query actually used
(q01 reserved 9857 MB for a peak of 875 MB).

Over-provisioning is not free. The estimate picks the T-shirt bucket, the bucket
decides how many statements fit at once, and the admission floor a waiter parks
for is a fraction of the same number -- so a 10x estimate serializes an agent
that could have run six of these concurrently, and makes every waiter behind it
hold out for memory nobody needs.

This is SQL Server's Memory Grant Feedback applied to that problem: once a shape
has run, stop guessing and use what it measured. The same idea appears in
Impala's ``MAX_MEM_ESTIMATE_FOR_ADMISSION`` (IMPALA-6847), for the same reason --
an estimator that cannot be trusted should not be the thing that gates
concurrency.

Deliberately *not* SQL Server's full mechanism: no percentile mode, no
oscillation detector, no persistence across restarts. Those exist to tame
parameter-sensitive plans over months of history; this keeps a handful of recent
observations in memory and takes their maximum, which is the conservative
reading of the same signal.

## Which number is the measurement

Only ``peak_memory_bytes`` (DuckDB's ``system_peak_buffer_memory``) tracks what a
query really holds. Two alternatives were measured on 1.5.5 and rejected:

- ``memory_allocated_bytes`` (``total_memory_allocated``) is per-statement, which
  makes it tempting, but it does not count the buffer-manager blocks an Iceberg
  scan holds. On the SF10 run it read **12 MB for a q01 whose real peak was
  876 MB**, and 0 MB for q06. Sizing a grant from it would OOM the query outright.
- ``peak_memory_bytes`` is a *connection-lifetime* high-water mark, which the
  runner turns into "how much this statement raised the bar" (see
  ``runner._apply_watermarks``). That delta equals the statement's true peak only
  on a connection that has not run a profiled statement yet; after that it is a
  lower bound and reads 0 for anything that stays under an earlier peak.

So an observation is recorded only when the caller can vouch for it -- the
connection's watermark was zero going in. On the fresh-session-per-query
workload where this pathology shows up that is every statement; on a long-held
session it is the first one, and the rest simply teach nothing rather than
teaching a wrong number.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from agent.executor.estimate_cache import EstimateKey

# How many recent peaks to keep per shape. The suggestion is their maximum, so
# this is purely a guard against one unrepresentative run driving admission for
# the whole TTL -- a query whose peak depends on a predicate's selectivity can
# measure small once and be sized from that until the entry expires. Three is
# enough to need three consecutive small runs before the grant drops, and small
# enough that a query that genuinely got cheaper is re-learned quickly.
_HISTORY = 3


@dataclass
class _Entry:
    peaks: deque[int] = field(default_factory=lambda: deque(maxlen=_HISTORY))
    expires_at: float = 0.0


class GrantFeedback:
    """Recent measured peaks per statement shape. Not thread-safe: like the rest
    of the sizing path it is touched only from the event loop."""

    def __init__(self, *, ttl_s: float, max_entries: int, safety: float) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max(1, max_entries)
        self._safety = safety
        self._entries: OrderedDict[EstimateKey, _Entry] = OrderedDict()

    def observe(
        self, key: EstimateKey, *, peak_bytes: int, granted_bytes: int, spilled: bool
    ) -> None:
        """Record one trustworthy measurement of what this shape used.

        ``spilled`` inverts the meaning of ``peak_bytes``. DuckDB caps buffer
        memory at the grant and spills the rest to disk, so a statement that
        spilled did not measure its need -- it measured its grant. Learning that
        peak would pin the shape at the size that was already too small and it
        would spill forever. Record the grant itself instead, which the safety
        multiplier then pushes to the next bucket up: that is the "grow on spill"
        half of memory grant feedback, and it is the only path by which a
        remembered grant gets larger.
        """
        if peak_bytes <= 0 and not spilled:
            # Nothing was measured. A statement that held no buffer memory teaches
            # us nothing about a shape that might hold plenty on other data, and
            # recording 0 would size the next one at the smallest bucket.
            return
        entry = self._entries.get(key)
        if entry is None or time.monotonic() >= entry.expires_at:
            entry = _Entry()
        entry.peaks.append(max(peak_bytes, granted_bytes) if spilled else peak_bytes)
        entry.expires_at = time.monotonic() + self._ttl_s
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def suggest(self, key: EstimateKey) -> int | None:
        """Bytes to size this shape from, or ``None`` if it has not been measured.

        The maximum of the remembered peaks, plus a safety margin. Maximum rather
        than mean or latest because under-granting costs a spill (or an OOM) and
        over-granting costs only concurrency this mechanism is handing back
        several times over.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return int(max(entry.peaks) * self._safety)

    def invalidate_all(self) -> None:
        """Drop everything, alongside the estimate cache. A statement that changes
        what a plan binds to changes what it will hold, too."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
