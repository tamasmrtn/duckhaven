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
