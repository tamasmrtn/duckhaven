"""Admission control: budget-bounded concurrency with a FIFO queue."""

import asyncio

import pytest

from agent.executor.admission import (
    Admission,
    QueuedTimeout,
    QueueFull,
    ReservationRequest,
)

# A 12-unit budget with no headroom makes the weighted slot maths exact:
# decaying_3 [3,2,1] -> slots of 6, 4, 2 (sum 12); cores 6 -> threads 3, 2, 1.
BUDGET = 12
CORES = 6


def _admission(profile="decaying_3", **kwargs):
    return Admission(
        profile=profile,
        headroom=0.0,
        mem_bytes_provider=lambda: BUDGET,
        cores_provider=lambda: CORES,
        **kwargs,
    )


async def test_admits_up_to_budget_then_queues():
    adm = _admission()
    r1 = await adm.acquire()
    r2 = await adm.acquire()
    r3 = await adm.acquire()
    # Slots are descending and sum to the budget; first query gets the most.
    assert (r1.memory_bytes, r2.memory_bytes, r3.memory_bytes) == (6, 4, 2)
    assert (r1.threads, r2.threads, r3.threads) == (3, 2, 1)
    assert adm.running_count == 3

    # The 4th query cannot be admitted (budget exhausted) -> it waits.
    pending = asyncio.create_task(adm.acquire())
    await asyncio.sleep(0)
    assert adm.queued_count == 1
    assert not pending.done()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


async def test_running_reservations_never_exceed_budget():
    adm = _admission()
    reservations = [await adm.acquire() for _ in range(3)]
    assert sum(r.memory_bytes for r in reservations) <= BUDGET
    # No 4th slot fits.
    assert adm._try_admit() is None


async def test_freed_slot_promotes_oldest_waiter_fifo():
    adm = _admission()
    r1 = await adm.acquire()  # 6
    await adm.acquire()  # 4
    await adm.acquire()  # 2  -> full

    first = asyncio.create_task(adm.acquire())
    await asyncio.sleep(0)
    second = asyncio.create_task(adm.acquire())
    await asyncio.sleep(0)
    assert adm.queued_count == 2

    adm.release(r1)  # frees the 6-byte slot
    await asyncio.sleep(0)
    # The oldest waiter is admitted into the largest free slot; the other waits.
    assert first.done() and not second.done()
    assert (await first).memory_bytes == 6
    assert adm.queued_count == 1

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second


async def test_largest_free_slot_is_reused():
    adm = _admission()
    r1 = await adm.acquire()  # 6
    r2 = await adm.acquire()  # 4
    adm.release(r1)  # free the 6 slot
    r3 = await adm.acquire()  # should reuse the largest free slot (6), not 2
    assert r3.memory_bytes == 6
    assert {r2.memory_bytes, r3.memory_bytes} == {4, 6}


async def test_single_profile_serializes_at_full_budget():
    adm = _admission(profile="single")
    r1 = await adm.acquire()
    assert r1.memory_bytes == BUDGET
    pending = asyncio.create_task(adm.acquire())
    await asyncio.sleep(0)
    assert adm.queued_count == 1
    adm.release(r1)
    await asyncio.sleep(0)
    assert (await pending).memory_bytes == BUDGET


async def test_set_profile_shrink_under_load_never_oversubscribes():
    """Switching to a profile with a bigger top slot must not overflow the budget
    while old reservations are still running."""
    adm = _admission(profile="equal_2")  # slots 6, 6
    r1 = await adm.acquire()  # 6, free = 6
    await adm.acquire()  # 6, free = 0

    adm.set_profile("single")  # new ladder wants a 12-byte slot
    # Nothing can be admitted: free budget is 0 even though the new slot exists.
    pending = asyncio.create_task(adm.acquire())
    await asyncio.sleep(0)
    assert adm.queued_count == 1

    adm.release(r1)  # free 6 -> still < 12, stays queued (no overflow)
    await asyncio.sleep(0)
    assert not pending.done()
    assert adm.active_profile == "single"

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


async def test_queue_full_rejects():
    adm = _admission(profile="single", max_queue_depth=1)
    r1 = await adm.acquire()  # running
    waiting = asyncio.create_task(adm.acquire())  # fills the 1-deep queue
    await asyncio.sleep(0)
    assert adm.queued_count == 1
    with pytest.raises(QueueFull):
        await adm.acquire()  # queue is full

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    adm.release(r1)


async def test_queued_timeout_fires():
    adm = _admission(profile="single", queued_timeout_s=0.05)
    await adm.acquire()  # occupies the only slot
    with pytest.raises(QueuedTimeout):
        await adm.acquire()  # waits, then times out
    assert adm.queued_count == 0


async def test_cancel_while_queued_does_not_disturb_running():
    adm = _admission(profile="single")
    r1 = await adm.acquire()
    waiting = asyncio.create_task(adm.acquire())
    await asyncio.sleep(0)
    assert adm.queued_count == 1

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert adm.queued_count == 0
    assert adm.running_count == 1  # the running query is untouched
    adm.release(r1)
    assert adm.running_count == 0


def test_unknown_profile_rejected():
    with pytest.raises(ValueError, match="Unknown concurrency profile"):
        _admission(profile="nonsense")


# -- auto profile (estimate-driven reservations) --------------------------


def _auto(**kwargs):
    # floor 1, ceiling = full budget so requests map 1:1 for exact assertions.
    return _admission(profile="auto", floor_bytes=1, ceiling_fraction=1.0, **kwargs)


def _req(memory_bytes, threads=1):
    return ReservationRequest(memory_bytes=memory_bytes, threads=threads)


async def test_auto_admits_requested_size():
    adm = _auto()
    assert adm.is_auto
    r = await adm.acquire(_req(5, threads=2))
    assert r.memory_bytes == 5
    assert r.threads == 2
    assert r.slot is None


async def test_auto_heterogeneous_never_exceeds_budget():
    adm = _auto()
    r1 = await adm.acquire(_req(7))  # committed 7
    r2 = await adm.acquire(_req(5))  # committed 12 (== budget)
    assert sum(r.memory_bytes for r in (r1, r2)) <= BUDGET
    # A third request does not fit -> queues.
    pending = asyncio.create_task(adm.acquire(_req(4)))
    await asyncio.sleep(0)
    assert adm.queued_count == 1
    assert not pending.done()
    # Freeing the small one still leaves only 5 free (< 4 fits now).
    adm.release(r2)
    await asyncio.sleep(0)
    assert (await pending).memory_bytes == 4


async def test_auto_clamps_to_floor_and_ceiling():
    adm = _admission(profile="auto", floor_bytes=3, ceiling_fraction=0.5)  # ceiling = 6
    small = await adm.acquire(_req(1))  # clamped up to floor 3
    assert small.memory_bytes == 3
    big = await adm.acquire(_req(BUDGET * 10))  # clamped down to ceiling 6
    assert big.memory_bytes == 6


async def test_auto_head_of_line_blocks_smaller_behind():
    """A queued large query is not skipped to admit a smaller one behind it."""
    adm = _auto()
    await adm.acquire(_req(6))
    r2 = await adm.acquire(_req(6))  # budget full
    big = asyncio.create_task(adm.acquire(_req(8)))
    await asyncio.sleep(0)
    small = asyncio.create_task(adm.acquire(_req(2)))
    await asyncio.sleep(0)
    assert adm.queued_count == 2

    adm.release(r2)  # frees 6: big (8) still does not fit; small (2) is behind it
    await asyncio.sleep(0)
    assert not big.done() and not small.done()

    for task in (big, small):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ── try_amend: resizing a live reservation ────────────────────────────────────
#
# A held SQL session reserves only a small idle baseline and grows to fit each
# statement it runs, then shrinks back. That needs the reservation to move while
# it is held, which `acquire`/`release` alone cannot express.


async def test_amend_grows_within_budget():
    adm = _auto()
    res = await adm.acquire(_req(2))

    assert adm.try_amend(res, _req(8, threads=4)) is True
    assert res.memory_bytes == 8
    assert res.threads == 4
    assert adm.committed_fraction == 8 / BUDGET


async def test_amend_grants_what_is_free_when_the_target_does_not_fit():
    """A partial grant beats refusing: the statement runs at the biggest size the
    agent can actually spare, which is never worse than the baseline it had."""
    adm = _auto()
    other = await adm.acquire(_req(7))  # 5 left
    res = await adm.acquire(_req(2))  # 3 left

    assert adm.try_amend(res, _req(10)) is False, "cannot be granted in full"
    assert res.memory_bytes == 5, "grew by exactly the free budget"
    assert adm.committed_fraction == 1.0
    adm.release(other)


async def test_amend_never_oversubscribes_the_budget():
    adm = _auto()
    a = await adm.acquire(_req(6))
    b = await adm.acquire(_req(6))
    for _ in range(5):
        adm.try_amend(a, _req(BUDGET))
        adm.try_amend(b, _req(BUDGET))
        assert adm.committed_fraction <= 1.0


async def test_amend_shrink_promotes_a_queued_waiter():
    """Shrinking back to the baseline must hand the freed budget to the queue,
    or a session that grew would starve everything waiting behind it."""
    adm = _auto()
    res = await adm.acquire(_req(10))
    waiting = asyncio.create_task(adm.acquire(_req(8)))
    await asyncio.sleep(0)
    assert adm.queued_count == 1

    adm.try_amend(res, _req(2))  # shrink back to baseline
    await asyncio.sleep(0)

    assert waiting.done(), "the shrink freed enough for the queued waiter"
    assert adm.queued_count == 0
    adm.release(await waiting)


async def test_amend_refuses_a_static_slot_reservation():
    """Static ladders hand out whole slots; there is no partial slot to trade."""
    adm = _admission(profile="single")
    res = await adm.acquire()
    before = res.memory_bytes

    assert adm.try_amend(res, _req(1)) is False
    assert res.memory_bytes == before


async def test_amend_clamps_to_the_ceiling():
    adm = _admission(profile="auto", floor_bytes=1, ceiling_fraction=0.5)
    res = await adm.acquire(_req(1))

    assert adm.try_amend(res, _req(BUDGET)) is True
    assert res.memory_bytes == BUDGET // 2


# ── threads are not a slice of the memory budget ──────────────────────────────


async def test_threads_are_the_whole_core_budget_regardless_of_memory():
    """Thread count used to be scaled by the reservation's share of *memory*, which
    floored every bucket below the largest to a single thread on a small agent.
    Nothing in the byte invariant depends on it, so a tiny reservation is entitled
    to the same CPU as a huge one."""
    adm = _auto()
    tiny = await adm.acquire(_req(1, threads=adm.threads_for_statement()))
    big = await adm.acquire(_req(BUDGET - 1, threads=adm.threads_for_statement()))

    assert adm.threads_for_statement() == CORES
    assert tiny.threads == big.threads == CORES


# ── elastic (revocable cache) memory ──────────────────────────────────────────
#
# `memory_bytes` is what a query must have; `elastic_bytes` is idle budget lent
# to it for DuckDB's file cache and taken straight back when someone needs it.
# The invariant covers both tiers: sum(required + elastic) <= budget.


def _holder(idle=True):
    """Callbacks a session supplies so its grant counts as reclaimable."""
    resized: list[int] = []
    return resized, (lambda: idle), resized.append


async def test_elastic_tops_up_from_idle_budget_only():
    adm = _auto()
    res = await adm.acquire(_req(2))

    assert adm.grant_elastic(res, 8) == 8
    assert (res.memory_bytes, res.elastic_bytes, res.total_bytes) == (2, 8, 10)
    # Only 2 left, so a second holder gets what is free and not a byte more.
    other = await adm.acquire(_req(1))
    assert adm.grant_elastic(other, 8) == 1
    assert adm.committed_fraction == 1.0


async def test_elastic_never_pushes_committed_past_the_budget():
    adm = _auto()
    reservations = [await adm.acquire(_req(1)) for _ in range(4)]
    for res in reservations:
        adm.grant_elastic(res, BUDGET)
        assert adm.committed_fraction <= 1.0
    assert sum(r.total_bytes for r in reservations) <= BUDGET


async def test_a_required_reservation_reclaims_idle_cache_instead_of_queueing():
    """The point of the tier: cache never makes a query that needs the memory wait."""
    adm = _auto()
    hog = await adm.acquire(_req(2))
    resized, is_idle, on_resize = _holder()
    hog.is_idle, hog.on_resize = is_idle, on_resize
    adm.grant_elastic(hog, 10)  # hog now holds the whole budget, 10 of it as cache
    assert adm.committed_fraction == 1.0

    res = await adm.acquire(_req(6))  # would have queued forever before

    assert res.memory_bytes == 6
    assert hog.elastic_bytes == 4, "took exactly what was needed, no more"
    assert resized == [hog.total_bytes], "the holder's connection was actually resized"
    assert adm.committed_fraction <= 1.0


async def test_reclaim_skips_a_holder_that_is_running_a_statement():
    """A busy holder's memory is genuinely in use; it gives its grant back itself
    when the statement finishes."""
    adm = _auto()
    busy = await adm.acquire(_req(2))
    busy_resized, _, busy_resize = _holder()
    busy.is_idle, busy.on_resize = (lambda: False), busy_resize
    adm.grant_elastic(busy, 5)
    idle = await adm.acquire(_req(1))
    idle_resized, idle_is_idle, idle_resize = _holder()
    idle.is_idle, idle.on_resize = idle_is_idle, idle_resize
    adm.grant_elastic(idle, 4)
    assert adm.committed_fraction == 1.0

    pending = asyncio.create_task(adm.acquire(_req(4)))
    await asyncio.sleep(0)

    assert busy.elastic_bytes == 5 and busy_resized == [], "the busy holder was untouched"
    assert idle.elastic_bytes == 0 and idle_resized == [idle.total_bytes]
    assert (await pending).memory_bytes == 4


async def test_reclaim_ignores_a_holder_it_cannot_resize():
    """Revoking memory the holder cannot actually hand back would break the
    invariant in the only direction that matters."""
    adm = _auto()
    orphan = await adm.acquire(_req(2))
    adm.grant_elastic(orphan, 10)  # no is_idle/on_resize wired up
    assert adm.committed_fraction == 1.0

    pending = asyncio.create_task(adm.acquire(_req(4)))
    await asyncio.sleep(0)

    assert orphan.elastic_bytes == 10
    assert adm.queued_count == 1
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


async def test_revoke_elastic_is_accounting_only():
    """The owner re-grants immediately afterwards, so touching the connection here
    would evict the file cache the grant exists to hold."""
    adm = _auto()
    res = await adm.acquire(_req(2))
    resized, is_idle, on_resize = _holder()
    res.is_idle, res.on_resize = is_idle, on_resize
    adm.grant_elastic(res, 8)

    assert adm.revoke_elastic(res) == 8
    assert res.elastic_bytes == 0
    assert resized == [], "nothing was applied to the connection"
    assert adm.committed_fraction == 2 / BUDGET


async def test_release_returns_both_tiers():
    adm = _auto()
    res = await adm.acquire(_req(2))
    adm.grant_elastic(res, 8)
    adm.release(res)
    assert adm.committed_fraction == 0.0
    assert adm.running_count == 0


async def test_committed_never_exceeds_budget_under_random_churn():
    """The invariant `auto` exists for, exercised the way sessions actually move:
    acquire a baseline, grow for a statement, take a cache top-up, hand it back,
    shrink, release. If any of those paths forgets to keep `_committed` in step
    with the reservation, the agent silently oversubscribes its cgroup and DuckDB
    gets OOM-killed.

    `_committed` is checked against the reservations themselves, not just against
    the budget: a counter that drifts low still passes a `<= budget` assertion
    while handing out memory that is already spoken for."""
    import random

    rng = random.Random(1234)
    adm = _auto()
    live = []

    def _track(res):
        # Half the holders look reclaimable, half look busy, so the churn covers
        # both sides of the reclaim predicate.
        res.is_idle = (lambda: rng.random() < 0.5) if rng.random() < 0.75 else None
        res.on_resize = (lambda _total: None) if res.is_idle is not None else None
        return res

    for _ in range(600):
        action = rng.choice(("acquire", "grow", "shrink", "lend", "reclaim", "release"))
        if action == "acquire" and len(live) < 6:
            free = int((1 - adm.committed_fraction) * BUDGET)
            if free >= 1:
                live.append(_track(await adm.acquire(_req(rng.randint(1, free)))))
        elif action == "grow" and live:
            adm.try_amend(rng.choice(live), _req(rng.randint(1, BUDGET)))
        elif action == "shrink" and live:
            adm.try_amend(rng.choice(live), _req(1))
        elif action == "lend" and live:
            adm.grant_elastic(rng.choice(live), rng.randint(0, BUDGET))
        elif action == "reclaim" and live:
            adm.revoke_elastic(rng.choice(live))
        elif action == "release" and live:
            adm.release(live.pop(rng.randrange(len(live))))

        assert 0.0 <= adm.committed_fraction <= 1.0, "budget oversubscribed"
        assert adm._committed == sum(r.total_bytes for r in live), "counter drifted"  # noqa: SLF001
        assert adm.running_count == len(live)

    for res in live:
        adm.release(res)
    assert adm.committed_fraction == 0.0
