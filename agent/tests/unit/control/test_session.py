"""Held SQL-session lifecycle on the agent: OPEN_SESSION holds a connection +
admission slot, EXEC_STATEMENT runs on the held connection without closing it,
CLOSE_SESSION / reconnect release the slot."""

import asyncio
import time

import pytest

from agent.control import session
from agent.executor.admission import Admission
from duckhaven_shared.protocol import Frame, FrameType


def _admission(profile: str = "single", **kwargs) -> Admission:
    return Admission(
        profile=profile,
        headroom=0.0,
        mem_bytes_provider=lambda: 1024**3,
        cores_provider=lambda: 2,
        **kwargs,
    )


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, msg: str) -> None:
        self.sent.append(msg)


def _last(ws: _FakeWS) -> Frame:
    return Frame.model_validate_json(ws.sent[-1])


@pytest.fixture(autouse=True)
def _clear_sessions():
    # The session registry is process-global; keep tests isolated.
    session._sessions.clear()
    yield
    session._sessions.clear()


async def test_open_session_holds_conn_and_reservation(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    ws = _FakeWS()
    await ch._handle_open_session(ws, {"session_id": "s1"}, admission)

    opened = _last(ws)
    assert opened.type == FrameType.SESSION_OPENED
    assert opened.payload["status"] == "open"
    assert session.count() == 1
    # The session occupies an admission slot for its lifetime.
    assert admission.running_count == 1


async def test_held_session_pins_duckdb_to_the_granted_bytes(tmp_path):
    """The session's fixed slice must be the bytes admission granted, in GiB.

    Same trap as the per-query path: the value is bytes/1024**3 (GiB) and DuckDB
    reads a `GB` suffix as 10**9, so labelling it GB silently handed a held
    session ~7% less memory than it had reserved -- and a session's limit is
    fixed at open, so it could never recover it.
    """
    import agent.control.channel as ch

    admission = _admission(profile="single")
    ws = _FakeWS()
    await ch._handle_open_session(ws, {"session_id": "s-mem"}, admission)

    held = session.get("s-mem")
    granted = held.reservation.memory_bytes
    setting = held.conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]

    assert setting == f"{granted // 1024**3}.0 GiB"


async def test_exec_statement_runs_on_held_conn_without_closing(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    ws = _FakeWS()
    await ch._handle_open_session(ws, {"session_id": "s1"}, admission)

    ws2 = _FakeWS()
    await ch._handle_exec_statement(
        ws2, {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 42 AS n"}, tmp_path, admission
    )
    done = _last(ws2)
    assert done.type == FrameType.QUERY_DONE
    assert done.payload["status"] == "done"
    assert done.payload["row_count"] == 1
    assert (tmp_path / "stmt1.parquet").exists()

    # The held connection is still usable — a second statement runs against the
    # same session state, proving EXEC did not close the connection.
    ws3 = _FakeWS()
    await ch._handle_exec_statement(
        ws3, {"session_id": "s1", "query_id": "stmt2", "sql": "SELECT 1 AS n"}, tmp_path, admission
    )
    assert _last(ws3).payload["status"] == "done"


async def test_exec_statement_unknown_session_fails(tmp_path):
    import agent.control.channel as ch

    ws = _FakeWS()
    await ch._handle_exec_statement(
        ws, {"session_id": "missing", "query_id": "x", "sql": "SELECT 1"}, tmp_path, _admission()
    )
    done = _last(ws)
    assert done.type == FrameType.QUERY_DONE
    assert done.payload["status"] == "failed"
    assert "session not found" in done.payload["error"]


async def test_close_session_releases_reservation(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    ws = _FakeWS()
    await ch._handle_open_session(ws, {"session_id": "s1"}, admission)
    assert admission.running_count == 1

    ws2 = _FakeWS()
    await ch._handle_close_session(ws2, {"session_id": "s1"}, admission)
    closed = _last(ws2)
    assert closed.type == FrameType.SESSION_CLOSED
    assert session.count() == 0
    assert admission.running_count == 0


# ── Non-blocking close (#158) ─────────────────────────────────────────────────
# Closing a session used to call DuckDB's blocking close() directly on the
# event-loop thread: with a statement mid-flight, close() waits for it to finish
# rather than interrupting it, freezing the whole agent (every other session,
# heartbeats, metrics) for as long as that statement would otherwise have run.
# Teardown must now interrupt the statement, wait for it via the session lock
# (never race close() against it), and run close() off the event loop.


class _SpyConn:
    """Wraps a real DuckDB connection: records interrupt/close call order and
    optionally delays close() to simulate DuckDB's close() blocking on an
    in-flight query."""

    def __init__(self, conn, close_delay: float = 0.0) -> None:
        self._conn = conn
        self.calls: list[str] = []
        self._close_delay = close_delay

    def interrupt(self) -> None:
        self.calls.append("interrupt")
        self._conn.interrupt()

    def close(self) -> None:
        self.calls.append("close")
        if self._close_delay:
            time.sleep(self._close_delay)
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


async def test_teardown_interrupts_before_closing(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    spy = _SpyConn(state.conn)
    state.conn = spy

    assert await session.remove("s1", admission) is True
    assert spy.calls == ["interrupt", "close"]


async def test_teardown_close_does_not_block_the_event_loop(tmp_path):
    """The regression test: against the old synchronous _teardown, a slow
    close() would starve every other coroutine on the loop, so the ticker below
    would make ~0 ticks instead of running alongside remove()."""
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    state.conn = _SpyConn(state.conn, close_delay=1.0)

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.05)
            ticks += 1

    ticker_task = asyncio.create_task(_ticker())
    start = time.monotonic()
    assert await session.remove("s1", admission) is True
    elapsed = time.monotonic() - start
    assert elapsed >= 1.0, f"close_delay did not actually run ({elapsed:.2f}s)"

    await ticker_task
    assert ticks >= 15, f"event loop was stalled during close() (only {ticks}/20 ticks)"


async def test_remove_awaits_the_session_lock_before_closing(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    spy = _SpyConn(state.conn)
    state.conn = spy

    await state.lock.acquire()
    try:
        task = asyncio.create_task(session.remove("s1", admission))
        await asyncio.sleep(0.05)
        # Interrupt fires immediately (independent of the lock); close waits.
        assert spy.calls == ["interrupt"]
        assert not task.done()
    finally:
        state.lock.release()

    assert await task is True
    assert spy.calls == ["interrupt", "close"]


async def test_remove_interrupts_a_genuinely_running_statement(tmp_path):
    """End-to-end proof at unit scope: a real long-running statement on the
    session's connection is stopped promptly by remove(), not run to completion,
    and resolves as a failed QUERY_DONE rather than racing the close."""
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)

    # Minutes of work if uninterrupted (mirrors
    # agent/tests/unit/executor/test_runner.py::test_timeout_interrupts_running_query);
    # timeout_s is generous so only the close-triggered interrupt can be
    # responsible for stopping it this fast.
    sql = "SELECT sum(t1.range + t2.range) FROM range(1000000) t1, range(1000000) t2"
    stmt_ws = _FakeWS()
    stmt_task = asyncio.create_task(
        ch._handle_exec_statement(
            stmt_ws,
            {"session_id": "s1", "query_id": "slow1", "sql": sql, "timeout_s": 300},
            tmp_path,
            admission,
        )
    )
    await asyncio.sleep(0.2)
    assert not stmt_task.done()

    start = time.monotonic()
    assert await session.remove("s1", admission) is True
    elapsed = time.monotonic() - start
    assert elapsed < 10, (
        f"remove() did not interrupt the running statement promptly ({elapsed:.1f}s)"
    )

    await stmt_task
    assert _last(stmt_ws).payload["status"] == "failed"
    assert session.count() == 0
    assert admission.running_count == 0


async def test_clear_all_on_reconnect_releases_slots(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    assert admission.running_count == 1

    await session.clear_all(admission)
    assert session.count() == 0
    assert admission.running_count == 0


async def test_sweep_reaps_idle_session_and_frees_slot(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    session.get("s1").last_active_at -= 10_000  # age past any idle timeout

    reaped = await session.sweep_expired(admission, idle_s=1.0, max_life_s=0)
    assert reaped == ["s1"]
    assert session.count() == 0
    assert admission.running_count == 0


async def test_sweep_reaps_over_max_lifetime(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    session.get("s1").opened_at -= 10_000  # aged past max lifetime, but not idle

    reaped = await session.sweep_expired(admission, idle_s=0, max_life_s=1.0)
    assert reaped == ["s1"]
    assert admission.running_count == 0


async def test_sweep_skips_session_running_a_statement(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    state.last_active_at -= 10_000  # idle by the clock, but a statement is executing
    await state.lock.acquire()
    try:
        assert await session.sweep_expired(admission, idle_s=1.0, max_life_s=0) == []
        assert session.count() == 1
        assert admission.running_count == 1
    finally:
        state.lock.release()


async def test_sweep_keeps_active_session(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    # A recent statement touched the session, so it is not idle.
    await ch._handle_exec_statement(
        _FakeWS(), {"session_id": "s1", "query_id": "q1", "sql": "SELECT 1"}, tmp_path, admission
    )

    assert await session.sweep_expired(admission, idle_s=60.0, max_life_s=0) == []
    assert session.count() == 1


async def test_sweep_is_idempotent_after_remove(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    session.get("s1").last_active_at -= 10_000

    assert await session.remove("s1", admission) is True
    assert admission.running_count == 0
    # A sweep after the session is already gone must not double-release the slot.
    assert await session.sweep_expired(admission, idle_s=1.0, max_life_s=0) == []
    assert admission.running_count == 0


async def test_push_metrics_emits_self_reap_close(monkeypatch):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    session.get("s1").last_active_at -= 10_000
    monkeypatch.setattr(ch.settings, "metrics_sample_interval_s", 0)
    monkeypatch.setattr(ch.settings, "session_idle_timeout_s", 1.0)
    monkeypatch.setattr(ch.settings, "session_max_lifetime_s", 0)

    class _Stop(Exception):
        pass

    class _Sample:
        def model_dump(self, mode="json"):
            return {}

    class _Sampler:
        def sample(self, **kwargs):
            return _Sample()

    class _WS(_FakeWS):
        async def send(self, msg: str) -> None:
            self.sent.append(msg)
            # Break the loop once we reach the per-tick metrics push.
            if Frame.model_validate_json(msg).type == FrameType.METRICS_SAMPLE:
                raise _Stop

    ws = _WS()
    with pytest.raises(_Stop):
        await ch._push_metrics(ws, _Sampler(), admission)

    reaps = [
        f for m in ws.sent if (f := Frame.model_validate_json(m)).type == FrameType.SESSION_CLOSED
    ]
    assert reaps and reaps[0].payload["reason"] == "agent_self_reap"
    assert session.count() == 0
    assert admission.running_count == 0


# ── STATEMENT_ACK receipt (#156) ──────────────────────────────────────────────
# A lost EXEC_STATEMENT frame used to leave the statement queued forever with no
# signal anywhere. The ack is the receipt that makes "never arrived"
# distinguishable from "still running", so it must be sent on arrival — before
# anything that can block or fail.


def _frames(ws: _FakeWS) -> list[Frame]:
    return [Frame.model_validate_json(m) for m in ws.sent]


async def test_exec_statement_acks_before_running(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)

    ws = _FakeWS()
    await ch._handle_exec_statement(
        ws, {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 1 AS n"}, tmp_path, admission
    )
    types = [f.type for f in _frames(ws)]
    assert types == [FrameType.STATEMENT_ACK, FrameType.QUERY_DONE]
    assert _frames(ws)[0].payload["query_id"] == "stmt1"


async def test_exec_statement_acks_before_taking_the_session_lock(tmp_path):
    """The ack must mean "the frame arrived", not "the statement started" — so it
    is sent even while another statement holds the session lock. Otherwise a
    statement queued behind a slow one would be reaped as undelivered."""
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")

    ws = _FakeWS()
    await state.lock.acquire()
    try:
        task = asyncio.create_task(
            ch._handle_exec_statement(
                ws,
                {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 1"},
                tmp_path,
                admission,
            )
        )
        # Let the handler run up to the lock, where it must now block.
        await asyncio.sleep(0.05)
        assert not task.done()
        assert [f.type for f in _frames(ws)] == [FrameType.STATEMENT_ACK]
    finally:
        state.lock.release()
    await task
    assert _frames(ws)[-1].payload["status"] == "done"


async def test_exec_statement_acks_even_when_session_missing(tmp_path):
    """Receipt is not success: an unknown session still acks, then fails."""
    import agent.control.channel as ch

    ws = _FakeWS()
    await ch._handle_exec_statement(
        ws, {"session_id": "missing", "query_id": "x", "sql": "SELECT 1"}, tmp_path, _admission()
    )
    frames = _frames(ws)
    assert [f.type for f in frames] == [FrameType.STATEMENT_ACK, FrameType.QUERY_DONE]
    assert frames[1].payload["status"] == "failed"


def test_capabilities_advertise_statement_ack():
    """The API gates the short ack deadline on this feature; without it an older
    agent's statements would all be reaped as undelivered."""
    import agent.control.channel as ch

    assert "statement_ack" in ch._PROTOCOL_FEATURES


# ── a stale reclaim of an idle session's elastic grant must never deadlock ────
#
# `Admission._reclaim_elastic` can queue a session's reservation into
# `_pending_resizes` while it is idle. If that session then gets a new
# statement before the entry drains, `_resize_for_statement`/
# `_shrink_to_baseline` would previously drain their own stale entry via
# `apply_pending_resizes()` while already holding `state.lock` -- calling
# `resize_when_free`, which re-acquires that same non-reentrant lock.
# Permanent deadlock; `sweep_expired` skips locked sessions, so nothing
# recovers it short of an agent restart.


async def test_statement_does_not_deadlock_on_a_stale_reclaim_of_its_own_session(tmp_path):
    import agent.control.channel as ch

    admission = _admission(profile="auto")
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")

    # Give the idle session an elastic grant, then reclaim it directly (as a
    # concurrent admission decision would) without draining the pending resize
    # -- reproducing the exact stale-entry precondition.
    admission.grant_elastic(state.reservation, 32 * 1024 * 1024)
    assert state.is_idle()
    reclaimed = admission._reclaim_elastic(16 * 1024 * 1024)  # noqa: SLF001
    assert reclaimed > 0
    assert any(r is state.reservation for r in admission._pending_resizes)  # noqa: SLF001

    # A new statement on the same session must complete, not hang draining its
    # own stale entry against its own held lock.
    ws = _FakeWS()
    await asyncio.wait_for(
        ch._handle_exec_statement(
            ws,
            {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 1 AS n"},
            tmp_path,
            admission,
        ),
        timeout=5,
    )
    assert _last(ws).payload["status"] == "done"


# ── teardown closes the reclaim race window instead of merely tolerating it ──
#
# `is_idle()` reports True until `state.lock` is actually acquired inside
# `_teardown`, so a reclaim could previously queue a resize for a session in
# the gap between `conn.interrupt()` and taking the lock, landing after the
# connection is closed (caught by `apply_resize`'s own try/except, but a real
# gap). Unhooking the reclaim hooks before `interrupt()` instead of after
# `close()` makes the reservation ineligible as a reclaim target for the
# whole of teardown.


async def test_teardown_unhooks_reclaim_targeting_before_interrupting(tmp_path):
    import agent.control.channel as ch

    admission = _admission(profile="auto")
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    reservation = state.reservation

    seen_during_interrupt: list[tuple[object, object]] = []

    class _SpyConn:
        def __init__(self, conn) -> None:
            self._conn = conn

        def interrupt(self) -> None:
            seen_during_interrupt.append((reservation.is_idle, reservation.on_resize))
            self._conn.interrupt()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    state.conn = _SpyConn(state.conn)

    assert await session.remove("s1", admission) is True
    assert seen_during_interrupt == [(None, None)], (
        "reservation was still a reclaim target while teardown was interrupting it"
    )


# ── the post-statement schema refresh must never hang a session ──────────────
#
# A bare `run_in_executor` call for `refresh_schema` had no bound: once enough
# EXPLAINs had spun and been abandoned to exhaust the estimate pool, it would
# queue behind workers that never return -- inside the session's own lock, so
# the session (and any CLOSE_SESSION on it) hung forever. Routed through the
# same capacity/timeout guard every other estimate-pool job uses, it must
# instead skip the refresh once the pool is exhausted.


async def test_refresh_schema_runs_after_a_use_statement(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")

    called = []
    state.refresh_schema = lambda: called.append(1)

    ws = _FakeWS()
    await asyncio.wait_for(
        ch._handle_exec_statement(
            ws,
            {"session_id": "s1", "query_id": "stmt1", "sql": "SET threads=2"},
            tmp_path,
            admission,
        ),
        timeout=5,
    )
    assert _last(ws).payload["status"] == "done"
    assert called, "did not refresh schema after a cheap (SET) statement"


class _NeverTouchConn:
    """Raises if anything calls a method on it -- the whole point of
    `discard_poisoned` is that the poisoned connection is never touched
    again, since an abandoned worker may still be running against it."""

    def __getattr__(self, name):
        raise AssertionError(f"poisoned connection was touched via .{name}()")


async def test_discard_poisoned_drops_the_session_without_touching_its_connection(tmp_path):
    import agent.control.channel as ch

    admission = _admission(profile="auto")
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    state.conn = _NeverTouchConn()
    running_before = admission.running_count

    assert await session.discard_poisoned("s1", admission) is True

    assert session.get("s1") is None
    assert admission.running_count == running_before - 1
    # Calling again (e.g. a duplicate discard) is a no-op, not an error.
    assert await session.discard_poisoned("s1", admission) is False


async def test_abandoned_statement_discards_the_session_without_shrinking_it(tmp_path, monkeypatch):
    """`_handle_exec_statement` must skip `_shrink_to_baseline`/schema refresh
    (both would touch the same connection an abandoned worker may still be
    running against) and discard the session instead of returning it to the
    pool of reusable connections."""
    import agent.control.channel as ch
    from agent.executor.supervisor import StatementAbandoned

    admission = _admission(profile="auto")
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    state.conn = _NeverTouchConn()

    async def fake_resize(*args, **kwargs):
        pass  # bypass resize entirely -- it isn't what this test is about

    async def fake_run_statement(*args, **kwargs):
        raise StatementAbandoned("statement exceeded timeout")

    monkeypatch.setattr(ch, "_resize_for_statement", fake_resize)
    monkeypatch.setattr(ch, "run_statement", fake_run_statement)

    ws = _FakeWS()
    await ch._handle_exec_statement(
        ws, {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 1"}, tmp_path, admission
    )

    assert _last(ws).payload["status"] == "failed"
    assert session.get("s1") is None, "poisoned session was left reusable"


async def test_refresh_schema_is_skipped_not_hung_when_the_estimate_pool_is_exhausted(
    tmp_path, monkeypatch
):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")

    called = []
    state.refresh_schema = lambda: called.append(1)
    # Every estimate-pool worker already lost to an abandoned planner.
    monkeypatch.setattr(ch, "_estimates_in_flight", max(2, ch.effective_cores()))

    ws = _FakeWS()
    await asyncio.wait_for(
        ch._handle_exec_statement(
            ws,
            {"session_id": "s1", "query_id": "stmt1", "sql": "SET threads=2"},
            tmp_path,
            admission,
        ),
        timeout=5,
    )

    assert _last(ws).payload["status"] == "done"
    assert not called, "refreshed schema despite the estimate pool being exhausted"
