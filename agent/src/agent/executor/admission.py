"""Memory-budget admission control with a FIFO queue (Snowflake-style).

The agent must never oversubscribe its memory: the sum of the ``memory_limit``
of all concurrently RUNNING DuckDB sessions must stay within the cgroup-aware
budget (``effective_memory_bytes() * (1 - headroom)``). Queries beyond that
capacity WAIT in a FIFO queue instead of being picked up — the agent throttles
rather than getting OOM-killed.

Capacity is a *weighted slot ladder* (see ``duckhaven_shared.concurrency``): the
budget is split across slots in proportion to descending weights, and a new query
takes the largest free slot. ``decaying_3`` ([3,2,1]) gives the first running
query the most memory/threads, the second less, the third least. DuckDB fixes
``memory_limit`` at session start and cannot resize a running query, so slots are
assigned statically at admission.

The profile is agent-global and switchable at runtime via ``set_profile`` (driven
by the worksheet ``SET duckhaven_concurrency`` command). The hard invariant —
``sum(running reservations) <= budget`` — is enforced by gating every admission
on a ``_committed`` byte counter, so it holds even while the profile changes
under load.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from agent.metrics.system import effective_cores, effective_memory_bytes
from duckhaven_shared.concurrency import CONCURRENCY_PROFILES, DEFAULT_PROFILE


class QueueFull(Exception):
    """The admission queue is at ``max_queue_depth``; the query is rejected."""


class QueuedTimeout(Exception):
    """The query waited longer than ``queued_timeout_s`` without being admitted."""


@dataclass
class _Slot:
    memory_bytes: int
    threads: int
    occupied: bool = False


@dataclass
class Reservation:
    """A grant returned by ``acquire`` and handed back to ``release``."""

    slot: _Slot
    memory_bytes: int
    threads: int


class Admission:
    """Async admission gate: bounds concurrent memory, queues the overflow FIFO."""

    def __init__(
        self,
        *,
        profile: str = DEFAULT_PROFILE,
        headroom: float = 0.10,
        max_queue_depth: int = 100,
        queued_timeout_s: float = 0.0,
        mem_bytes_provider=None,
        cores_provider=None,
    ) -> None:
        # Resolved at call time (not as default args) so the cgroup-aware helpers
        # are the single source of truth and stay monkeypatchable in tests.
        mem_bytes_provider = mem_bytes_provider or effective_memory_bytes
        cores_provider = cores_provider or effective_cores
        self._budget = max(1, int(mem_bytes_provider() * (1 - headroom)))
        self._cores = max(1, cores_provider())
        self._max_queue_depth = max_queue_depth
        self._queued_timeout_s = queued_timeout_s
        self._committed = 0
        self._running = 0
        self._waiters: deque[asyncio.Future] = deque()
        self.set_profile(profile)

    # -- profile -----------------------------------------------------------

    def set_profile(self, profile: str) -> None:
        """Rebuild the slot ladder for FUTURE admissions.

        Running queries keep their existing reservations; the ``_committed`` guard
        prevents the rebuilt ladder from oversubscribing during the transition.
        """
        weights = CONCURRENCY_PROFILES.get(profile)
        if weights is None:
            valid = ", ".join(CONCURRENCY_PROFILES)
            raise ValueError(f"Unknown concurrency profile '{profile}'. Valid: {valid}.")
        total = sum(weights)
        self._profile = profile
        self._slots = [
            _Slot(
                memory_bytes=max(1, round(self._budget * w / total)),
                threads=max(1, round(self._cores * w / total)),
            )
            for w in weights
        ]

    @property
    def active_profile(self) -> str:
        return self._profile

    @property
    def running_count(self) -> int:
        return self._running

    @property
    def queued_count(self) -> int:
        return len(self._waiters)

    # -- admission ---------------------------------------------------------

    def _try_admit(self) -> Reservation | None:
        """Grab the largest free slot that fits the remaining budget, or None."""
        free = self._budget - self._committed
        for slot in self._slots:  # descending by size
            if not slot.occupied and slot.memory_bytes <= free:
                slot.occupied = True
                self._committed += slot.memory_bytes
                self._running += 1
                return Reservation(slot, slot.memory_bytes, slot.threads)
        return None

    async def acquire(self) -> Reservation:
        """Admit immediately if capacity allows, else queue FIFO until it does.

        Raises ``QueueFull`` when the queue is at capacity, ``QueuedTimeout`` when
        the wait exceeds ``queued_timeout_s``. Cancellation while queued removes
        the waiter cleanly.
        """
        reservation = self._try_admit()
        if reservation is not None:
            return reservation

        if len(self._waiters) >= self._max_queue_depth:
            raise QueueFull("admission queue is full")

        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            if self._queued_timeout_s > 0:
                await asyncio.wait_for(waiter, self._queued_timeout_s)
            else:
                await waiter
            return waiter.result()
        except (TimeoutError, asyncio.CancelledError) as exc:
            # Removed from the queue before admission: drop the waiter. If we were
            # admitted in the same tick (result already set), hand the slot back.
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            elif waiter.done() and not waiter.cancelled():
                self.release(waiter.result())
            if isinstance(exc, TimeoutError):
                raise QueuedTimeout("query exceeded queued timeout") from exc
            raise

    def release(self, reservation: Reservation) -> None:
        """Return a reservation and promote the oldest waiter that now fits."""
        reservation.slot.occupied = False
        self._committed -= reservation.memory_bytes
        self._running -= 1
        self._promote()

    def _promote(self) -> None:
        """Admit queued waiters (oldest first) while capacity allows."""
        while self._waiters:
            reservation = self._try_admit()
            if reservation is None:
                return
            waiter = self._waiters.popleft()
            if waiter.cancelled():
                # The waiter went away between admit and wake; undo the grant
                # inline (not via release, to avoid re-entering _promote) and try
                # the next waiter.
                reservation.slot.occupied = False
                self._committed -= reservation.memory_bytes
                self._running -= 1
                continue
            waiter.set_result(reservation)
