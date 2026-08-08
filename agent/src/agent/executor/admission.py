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
class ReservationRequest:
    """A requested reservation size for the ``auto`` profile (estimate-driven)."""

    memory_bytes: int
    threads: int


@dataclass
class Reservation:
    """A grant returned by ``acquire`` and handed back to ``release``.

    ``slot`` is the fixed ladder slot for static profiles, or ``None`` for an
    ``auto`` reservation (sized from the query's estimate, no fixed slot).
    """

    slot: _Slot | None
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
        floor_bytes: int = 64 * 1024 * 1024,
        ceiling_fraction: float = 1.0,
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
        # Bounds for ``auto`` reservations so an estimate can never oversubscribe
        # the budget nor request a uselessly tiny slice.
        self._floor_bytes = min(floor_bytes, self._budget)
        self._ceiling_bytes = max(self._floor_bytes, int(ceiling_fraction * self._budget))
        self._committed = 0
        self._running = 0
        # Each waiter is queued with its request (None for static profiles).
        self._waiters: deque[tuple[asyncio.Future, ReservationRequest | None]] = deque()
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
        self._profile = profile
        # ``auto`` has no fixed ladder; reservations are sized per-query at
        # admission time (see ``acquire`` with a ReservationRequest).
        total = sum(weights)
        self._slots = (
            [
                _Slot(
                    memory_bytes=max(1, round(self._budget * w / total)),
                    threads=max(1, round(self._cores * w / total)),
                )
                for w in weights
            ]
            if total
            else []
        )

    @property
    def active_profile(self) -> str:
        return self._profile

    @property
    def is_auto(self) -> bool:
        return self._profile == "auto"

    @property
    def cores(self) -> int:
        return self._cores

    @property
    def budget_bytes(self) -> int:
        return self._budget

    @property
    def committed_fraction(self) -> float:
        """Share of the budget currently reserved (utilization in auto mode)."""
        return self._committed / self._budget

    @property
    def running_count(self) -> int:
        return self._running

    @property
    def queued_count(self) -> int:
        return len(self._waiters)

    # -- admission ---------------------------------------------------------

    def _clamp(self, memory_bytes: int) -> int:
        """Bound an ``auto`` request to ``[floor, ceiling]`` so it can never
        oversubscribe the budget yet always (eventually) fits."""
        return max(self._floor_bytes, min(memory_bytes, self._ceiling_bytes))

    def _try_admit(self, request: ReservationRequest | None = None) -> Reservation | None:
        """Admit a query if capacity allows, else None.

        Static profiles take the largest free ladder slot; ``auto`` requests take
        a clamped byte reservation. Both are gated on the ``_committed`` byte
        counter, so ``sum(running) <= budget`` holds regardless of profile.
        """
        free = self._budget - self._committed
        if request is not None:
            clamped = self._clamp(request.memory_bytes)
            if clamped <= free:
                self._committed += clamped
                self._running += 1
                return Reservation(None, clamped, request.threads)
            return None
        for slot in self._slots:  # descending by size
            if not slot.occupied and slot.memory_bytes <= free:
                slot.occupied = True
                self._committed += slot.memory_bytes
                self._running += 1
                return Reservation(slot, slot.memory_bytes, slot.threads)
        return None

    async def acquire(
        self,
        request: ReservationRequest | None = None,
        *,
        queued_timeout_s: float | None = None,
    ) -> Reservation:
        """Admit immediately if capacity allows, else queue FIFO until it does.

        ``request`` sizes an ``auto`` reservation; ``None`` uses the static slot
        ladder. Raises ``QueueFull`` when the queue is at capacity, ``QueuedTimeout``
        when the wait exceeds the queued timeout. Cancellation while queued
        removes the waiter cleanly.

        ``queued_timeout_s`` overrides the agent-wide ``queued_timeout_s`` for this
        caller only. Session opens use it because the control plane fails them at a
        deadline of its own, so waiting past that only turns a fast, actionable
        error into a long hang; queries keep the agent-wide setting.
        """
        reservation = self._try_admit(request)
        if reservation is not None:
            return reservation

        if len(self._waiters) >= self._max_queue_depth:
            raise QueueFull("admission queue is full")

        timeout_s = self._queued_timeout_s if queued_timeout_s is None else queued_timeout_s
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.append((waiter, request))
        try:
            if timeout_s > 0:
                await asyncio.wait_for(waiter, timeout_s)
            else:
                await waiter
            return waiter.result()
        except (TimeoutError, asyncio.CancelledError) as exc:
            # Removed from the queue before admission: drop the waiter. If we were
            # admitted in the same tick (result already set), hand the grant back.
            entry = next((e for e in self._waiters if e[0] is waiter), None)
            if entry is not None:
                self._waiters.remove(entry)
            elif waiter.done() and not waiter.cancelled():
                self.release(waiter.result())
            if isinstance(exc, TimeoutError):
                raise QueuedTimeout("exceeded queued timeout") from exc
            raise

    def release(self, reservation: Reservation) -> None:
        """Return a reservation and promote the oldest waiter that now fits."""
        if reservation.slot is not None:
            reservation.slot.occupied = False
        self._committed -= reservation.memory_bytes
        self._running -= 1
        self._promote()

    def _promote(self) -> None:
        """Admit queued waiters (oldest first) while the head-of-line fits.

        Head-of-line: we only admit the oldest waiter, preserving FIFO fairness
        with heterogeneous ``auto`` sizes (we never skip a large query to admit a
        smaller one behind it).
        """
        while self._waiters:
            waiter, request = self._waiters[0]
            if waiter.cancelled():
                self._waiters.popleft()
                continue
            reservation = self._try_admit(request)
            if reservation is None:
                return
            self._waiters.popleft()
            waiter.set_result(reservation)
