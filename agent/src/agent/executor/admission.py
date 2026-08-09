"""Memory-budget admission control with a FIFO queue (Snowflake-style).

The agent must never oversubscribe its memory: the sum of the ``memory_limit``
of all concurrently RUNNING DuckDB sessions must stay within the cgroup-aware
budget (``effective_memory_bytes() * (1 - headroom)``). Queries beyond that
capacity WAIT in a FIFO queue instead of being picked up — the agent throttles
rather than getting OOM-killed.

Capacity is a *weighted slot ladder* (see ``duckhaven_shared.concurrency``): the
budget is split across slots in proportion to descending weights, and a new query
takes the largest free slot. ``decaying_3`` ([3,2,1]) gives the first running
query the most memory, the second less, the third least. DuckDB fixes
``memory_limit`` at session start and cannot resize a running query, so slots are
assigned statically at admission. The weights divide memory only — every slot
gets the agent's full core count (see ``set_profile``).

The profile is agent-global and switchable at runtime via ``set_profile`` (driven
by the worksheet ``SET duckhaven_concurrency`` command). The hard invariant —
``sum(running reservations) <= budget`` — is enforced by gating every admission
on a ``_committed`` byte counter, so it holds even while the profile changes
under load.

A reservation has two tiers. ``memory_bytes`` is what the query **requires** not
to OOM; it is what ``acquire`` blocks on and it is never taken back.
``elastic_bytes`` is a **revocable** top-up handed out of the currently
uncommitted budget: DuckDB's ``memory_limit`` also sizes its
``EXTERNAL_FILE_CACHE``, so an object-storage-backed scan that is charged only
its operator working set re-reads and re-decompresses its Parquet on every pass
(measured on SF10: 8.1 CPU-seconds at 322 MB vs 1.36 at 3.6 GiB for identical
output). Elastic memory buys that cache back without weakening the gate, because
it can be reclaimed — lowering a connection's ``memory_limit`` evicts the cache
synchronously in ~12 ms. Both tiers are counted in ``_committed``, so the
invariant is really ``sum(required + elastic) <= budget``.

Thread count is deliberately **not** derived from either tier. DuckDB threads are
not a partitionable resource on a cgroup-capped agent — the CPU quota is the cap,
and the OS timeshares within it — so every statement is given the agent's full
core count (see ``threads_for_statement``).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent.metrics.system import effective_cores, effective_memory_bytes
from duckhaven_shared.concurrency import CONCURRENCY_PROFILES, DEFAULT_PROFILE

logger = logging.getLogger(__name__)


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
class _GrowthWaiter:
    """A statement parked until enough budget is free for it to grow into.

    ``needed`` is the extra bytes it must see free to be worth waking — the queue
    is head-of-line, so a waiter that cannot be satisfied blocks the ones behind
    it rather than letting a smaller request jump ahead.
    """

    future: asyncio.Future
    needed: int


@dataclass
class Reservation:
    """A grant returned by ``acquire`` and handed back to ``release``.

    ``slot`` is the fixed ladder slot for static profiles, or ``None`` for an
    ``auto`` reservation (sized from the query's estimate, no fixed slot).

    ``memory_bytes`` is the required, non-revocable floor; ``elastic_bytes`` is
    the revocable cache top-up on top of it. The connection's DuckDB
    ``memory_limit`` tracks ``total_bytes``, the sum of the two.

    ``is_idle``/``on_resize`` are supplied by the session layer (see
    ``agent.control.session.register``) for reservations that can take elastic:
    ``is_idle`` reports whether the connection is safe to resize *right now*
    (no statement running on it), and ``on_resize`` is an **async** hook that
    takes the session's lock and applies the new total to DuckDB off the event
    loop. A reservation missing either is never reclaimed from — revoking memory
    the holder cannot actually give back would break the invariant in the one
    direction that matters.
    """

    slot: _Slot | None
    memory_bytes: int
    threads: int
    elastic_bytes: int = 0
    is_idle: Callable[[], bool] | None = None
    on_resize: Callable[[int], Awaitable[None]] | None = None

    @property
    def total_bytes(self) -> int:
        """What this reservation's connection may actually use."""
        return self.memory_bytes + self.elastic_bytes


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
        # Live reservations currently holding a revocable elastic grant, in no
        # particular order (``_reclaim_elastic`` sorts when it needs to). A list
        # rather than a set because Reservation is an unhashable dataclass, and
        # membership is by identity.
        self._elastic: list[Reservation] = []
        # Reservations whose elastic grant has been reclaimed in accounting but
        # whose DuckDB connection has not been shrunk yet. Drained by
        # ``apply_pending_resizes``; see there for why this is not done inline.
        self._pending_resizes: list[Reservation] = []
        # Statements parked waiting for room to grow, oldest first. A deque of
        # futures rather than a broadcast Event: waking every waiter on every
        # free had them re-race and split the same bytes into fractions too small
        # for any of them to use, so they parked again still holding what they
        # took. See ``_promote_growth``.
        self._growth_queue: deque[_GrowthWaiter] = deque()
        self.set_profile(profile)

    # -- profile -----------------------------------------------------------

    def set_profile(self, profile: str) -> None:
        """Rebuild the slot ladder for FUTURE admissions.

        Running queries keep their existing reservations; the ``_committed`` guard
        prevents the rebuilt ladder from oversubscribing during the transition.

        A ladder's weights divide **memory** only. They used to divide the core
        count too, which on a small agent collapsed every multi-slot profile to
        one thread per query (``decaying_3`` on 2 cores: 1/1/1) — the same
        coupling ``threads_for_statement`` exists to undo for ``auto``, and for
        the same reason it buys nothing: the cgroup quota caps total CPU whatever
        the slots say, so a narrower slot only made its own query slower. A ladder
        also bounds concurrency by its slot count, so full cores per slot
        oversubscribes threads by a known, small factor rather than an open-ended
        one.
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
                    threads=self.threads_for_statement(),
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

    def threads_for_statement(self) -> int:
        """DuckDB thread count for one statement: the agent's whole CPU budget.

        One rule for every profile — the ``auto`` estimator's buckets and the
        static ladders' weights both size *memory* and neither touches this.

        Threads used to be derived from the reservation's share of the memory
        budget, which on a 2-core agent floored every ``auto`` bucket below XL and
        every multi-slot ladder to a single thread — 21 of the 22 TPC-H queries
        ran single-threaded. Nothing in the byte invariant depends on this number:
        the cgroup CPU quota is the real cap, and two concurrent statements at the
        full core count each were measured to finish in the same wall time as one
        running alone. So under-provisioning here is a pure loss and
        over-provisioning is free.
        """
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

    def _free(self) -> int:
        """Budget not committed to any running reservation."""
        return self._budget - self._committed

    def _admit_from_free(self, request: ReservationRequest | None) -> Reservation | None:
        """Admit strictly out of uncommitted budget, without reclaiming anything."""
        free = self._free()
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

    def _shortfall(self, request: ReservationRequest | None) -> int:
        """Bytes that must come free before ``request`` could be admitted.

        For a static ladder this is measured against the *smallest* free slot —
        the least eviction that makes any progress. ``_admit_from_free`` still
        picks the largest slot that fits once the budget has moved.
        """
        if request is not None:
            return self._clamp(request.memory_bytes) - self._free()
        free_slots = [slot.memory_bytes for slot in self._slots if not slot.occupied]
        return min(free_slots) - self._free() if free_slots else 0

    def _try_admit(self, request: ReservationRequest | None = None) -> Reservation | None:
        """Admit a query if capacity allows, else None.

        Static profiles take the largest free ladder slot; ``auto`` requests take
        a clamped byte reservation. Both are gated on the ``_committed`` byte
        counter, so ``sum(required + elastic) <= budget`` holds regardless of
        profile.

        When nothing fits, revocable elastic grants are reclaimed from *idle*
        holders and the admission is retried once. That is the whole point of the
        elastic tier: idle cache never queues a query that needs the memory.
        """
        reservation = self._admit_from_free(request)
        if reservation is not None:
            return reservation
        if self._reclaim_elastic(self._shortfall(request)) > 0:
            return self._admit_from_free(request)
        return None

    # -- elastic (revocable) memory ----------------------------------------

    def grant_elastic(self, reservation: Reservation, target_bytes: int) -> int:
        """Top a reservation up to ``target_bytes`` of revocable cache memory.

        Takes only what is uncommitted — this never queues, never evicts another
        holder, and never fails: a grant of 0 just means the agent is busy and the
        statement runs on its required floor, exactly as it would have before.
        Returns the bytes newly granted.

        Static ladder slots have no elastic tier (there is no partial slot to
        hand back), so they get 0.
        """
        if reservation.slot is not None:
            return 0
        target = max(0, min(target_bytes, self._budget - reservation.memory_bytes))
        granted = min(target - reservation.elastic_bytes, self._free())
        if granted <= 0:
            return 0
        reservation.elastic_bytes += granted
        self._committed += granted
        if not any(held is reservation for held in self._elastic):
            self._elastic.append(reservation)
        return granted

    def revoke_elastic(self, reservation: Reservation) -> int:
        """Give a reservation's whole elastic grant back, **accounting only**.

        Nothing is applied to the connection: the owner is the caller here, and
        it applies the final size itself once it has finished resizing (see
        ``channel._resize_for_statement``). That is deliberate — revoking and
        immediately re-granting the same bytes must not thrash the DuckDB file
        cache the grant exists to hold. Returns the bytes returned.
        """
        released = self._revoke(reservation, reservation.elastic_bytes, apply=False)
        if released:
            self._promote()
        return released

    def _revoke(self, reservation: Reservation, amount: int, *, apply: bool) -> int:
        """Take back up to ``amount`` of one reservation's elastic grant.

        ``apply`` drives the connection resize; it is True when we are taking the
        memory from someone who is not expecting it (reclaim) and False when the
        owner will apply its own new size. Never promotes — callers are either
        inside an admission decision (where ``_promote`` would recurse) or promote
        themselves afterwards.
        """
        give = min(amount, reservation.elastic_bytes)
        if give <= 0:
            return 0
        reservation.elastic_bytes -= give
        self._committed -= give
        self._promote_growth()
        if reservation.elastic_bytes == 0:
            self._elastic = [held for held in self._elastic if held is not reservation]
        if apply and not any(queued is reservation for queued in self._pending_resizes):
            self._pending_resizes.append(reservation)
        return give

    async def apply_pending_resizes(self) -> None:
        """Shrink the connections whose elastic memory was reclaimed.

        Reclaim happens inside ``_try_admit``/``try_amend``, which are synchronous
        and run on the event loop. Applying ``SET memory_limit`` there blocked the
        whole control channel — under a 22-way burst it fired on nearly every
        admission decision and the agent stopped answering heartbeats for seconds
        at a time. So the reclaim records the holder here and an async caller
        drains it: ``on_resize`` takes that session's lock and runs the ``SET`` on
        an executor thread.

        The cost is a window in which admission has already counted the bytes as
        free while DuckDB still holds them. It is bounded by one session's fair
        share of the budget (see ``channel._elastic_target``) and closed by the
        next drain — every async admission path drains, and the metrics loop
        drains as a backstop, so the window is at most one sampling interval.

        Best-effort per holder: one connection that will not shrink must not stop
        the others, and a failure leaves DuckDB *higher* than accounted, which the
        holder's next statement corrects when it sets its own limit.
        """
        pending, self._pending_resizes = self._pending_resizes, []
        for reservation in pending:
            if reservation.on_resize is None:
                continue
            try:
                await reservation.on_resize(reservation.total_bytes)
            except Exception as exc:  # noqa: BLE001 - one bad holder must not stall the rest
                logger.warning("Applying a reclaimed memory limit failed: %s", exc)

    def _reclaim_elastic(self, needed: int) -> int:
        """Reclaim up to ``needed`` bytes of elastic memory from idle holders.

        Largest grant first, so the fewest connections lose their cache. A holder
        mid-statement is skipped entirely — its memory is genuinely in use, and it
        returns its own grant when the statement ends anyway.
        """
        if needed <= 0:
            return 0
        holders = sorted(
            (
                held
                for held in self._elastic
                if held.on_resize is not None and held.is_idle is not None and held.is_idle()
            ),
            key=lambda held: held.elastic_bytes,
            reverse=True,
        )
        reclaimed = 0
        for holder in holders:
            reclaimed += self._revoke(holder, needed - reclaimed, apply=True)
            if reclaimed >= needed:
                break
        return reclaimed

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

    def try_amend(self, reservation: Reservation, request: ReservationRequest) -> bool:
        """Resize a live ``auto`` reservation in place. Returns whether it was
        granted in full.

        This is how a held session sizes itself to the statement it is about to
        run: grow to the estimate, then shrink back when the statement is done.
        Growth **never blocks** — the caller is already holding memory while
        asking for more, so waiting for the rest could deadlock two growing
        sessions against each other with nothing to break the tie. When the
        budget cannot cover the whole request the caller gets whatever is free
        and a ``False``, and decides for itself whether to run at that size.

        Both the reservation and ``_committed`` move together, which is what
        keeps ``release``'s ``_committed -= reservation.memory_bytes`` correct.
        Never assign to ``reservation.memory_bytes`` directly — the byte counter
        would drift and the budget invariant would silently break.
        """
        if reservation.slot is not None:
            # Static ladders hand out fixed slots; there is no partial slot to
            # give back or take. The session keeps the slot it was admitted with.
            return False

        previous = reservation.memory_bytes
        target = self._clamp(request.memory_bytes)
        if target > previous + self._free():
            # Required memory outranks cache: take back what idle holders are
            # only using to cache with before settling for a partial grant.
            self._reclaim_elastic(target - previous - self._free())
        granted = min(target, previous + self._free())

        self._committed += granted - previous
        reservation.memory_bytes = granted
        reservation.threads = max(1, request.threads)
        if granted < previous:
            # A shrink frees budget the queues may already be waiting on.
            self._promote()
            self._promote_growth()
        return granted >= target

    # -- waiting for room to grow -------------------------------------------

    @property
    def growth_waiting(self) -> int:
        """Statements currently parked waiting for budget to grow into."""
        return len(self._growth_queue)

    async def await_growth(self, needed: int, timeout: float) -> bool:
        """Park until ``needed`` bytes are free, or ``timeout`` elapses.

        Returns whether budget actually came free. The caller is expected to have
        given its own grant back down to a baseline *before* calling — a waiter
        that sleeps holding a partial grant is holding exactly the budget it, and
        everyone behind it, is waiting for. Ten of them once held 100.000% of a
        4 GiB agent's budget between them while all ten waited for more, and only
        the timeout broke it.

        Cancellation propagates rather than being swallowed, matching ``acquire``:
        a CANCEL_QUERY or a session teardown arriving mid-wait has to actually stop
        the statement, not be absorbed here and let it run anyway.
        """
        if timeout <= 0:
            return False
        waiter = _GrowthWaiter(asyncio.get_running_loop().create_future(), max(0, needed))
        self._growth_queue.append(waiter)
        try:
            # The result carries *why* we woke: True from ``_promote_growth`` (the
            # budget is there), False from ``release_growth_head`` (it never will
            # be — run with what you have). Returning a bare True here would make
            # the two indistinguishable, and the caller would park straight back.
            return await asyncio.wait_for(waiter.future, timeout)
        except TimeoutError:
            return False
        finally:
            entry = next((e for e in self._growth_queue if e is waiter), None)
            if entry is not None:
                self._growth_queue.remove(entry)

    def release_growth_head(self) -> bool:
        """Let the oldest parked statement give up waiting and run as it is.

        The escape hatch for a queue that can no longer be served: when nothing is
        executing, no budget will ever be released, and every parked statement
        would otherwise sleep until its own deadline. The guard in
        ``channel._resize_for_statement`` catches that *before* a statement parks;
        this catches the case where the system went quiet *after* it parked, which
        is what actually happened — ten statements sat for 255 seconds on an agent
        that had stopped doing anything at all.

        One at a time, deliberately: the released statement runs, finishes and
        releases, and the ordinary ``_promote_growth`` path then serves the next
        one properly. Waking the whole queue instead would put every starved
        statement on the agent at the same instant, which is the herd that killed
        it last time. Returns whether a waiter was released.
        """
        while self._growth_queue:
            head = self._growth_queue.popleft()
            if head.future.done():
                continue
            # False == "budget did not come free; proceed with what you have".
            head.future.set_result(False)
            return True
        return False

    def _promote_growth(self) -> None:
        """Wake **one** parked statement, if the head of the queue now fits.

        One at a time and strictly in order, for the same reason ``_promote``
        admits one at a time: waking everybody hands each of them a slice of the
        free budget, and a slice is usually below the floor they parked for. The
        head waiter instead sees the whole of what is free, uses it, runs, and
        releases — which is what makes progress structural rather than lucky.

        Head-of-line: a head waiter that still does not fit blocks the queue
        rather than letting a smaller request behind it jump ahead.
        """
        while self._growth_queue:
            head = self._growth_queue[0]
            if head.future.done():
                self._growth_queue.popleft()
                continue
            if head.needed > self._free():
                return
            self._growth_queue.popleft()
            head.future.set_result(True)
            return

    def release(self, reservation: Reservation) -> None:
        """Return a reservation — both tiers — and promote the oldest waiter that
        now fits.

        The elastic grant is dropped by accounting alone: the connection it
        belonged to is being closed, so there is nothing left to resize.
        """
        if reservation.slot is not None:
            reservation.slot.occupied = False
        self._committed -= reservation.total_bytes
        reservation.elastic_bytes = 0
        self._elastic = [held for held in self._elastic if held is not reservation]
        self._running -= 1
        # New admissions first, so the FIFO `_promote` documents stays the one
        # that orders arrivals; a growth waiter is already holding a session, and
        # admissions are baseline-sized, so this cannot meaningfully starve it.
        self._promote()
        self._promote_growth()

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
