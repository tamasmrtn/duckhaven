"""Held SQL-session lifecycle on the agent: OPEN_SESSION holds a connection +
admission slot, EXEC_STATEMENT runs on the held connection without closing it,
CLOSE_SESSION / reconnect release the slot."""

import asyncio

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


async def test_exec_statement_runs_on_held_conn_without_closing(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    ws = _FakeWS()
    await ch._handle_open_session(ws, {"session_id": "s1"}, admission)

    ws2 = _FakeWS()
    await ch._handle_exec_statement(
        ws2, {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 42 AS n"}, tmp_path
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
        ws3, {"session_id": "s1", "query_id": "stmt2", "sql": "SELECT 1 AS n"}, tmp_path
    )
    assert _last(ws3).payload["status"] == "done"


async def test_exec_statement_unknown_session_fails(tmp_path):
    import agent.control.channel as ch

    ws = _FakeWS()
    await ch._handle_exec_statement(
        ws, {"session_id": "missing", "query_id": "x", "sql": "SELECT 1"}, tmp_path
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


async def test_clear_all_on_reconnect_releases_slots(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    assert admission.running_count == 1

    session.clear_all(admission)
    assert session.count() == 0
    assert admission.running_count == 0


async def test_sweep_reaps_idle_session_and_frees_slot(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    session.get("s1").last_active_at -= 10_000  # age past any idle timeout

    reaped = session.sweep_expired(admission, idle_s=1.0, max_life_s=0)
    assert reaped == ["s1"]
    assert session.count() == 0
    assert admission.running_count == 0


async def test_sweep_reaps_over_max_lifetime(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    session.get("s1").opened_at -= 10_000  # aged past max lifetime, but not idle

    reaped = session.sweep_expired(admission, idle_s=0, max_life_s=1.0)
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
        assert session.sweep_expired(admission, idle_s=1.0, max_life_s=0) == []
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
        _FakeWS(), {"session_id": "s1", "query_id": "q1", "sql": "SELECT 1"}, tmp_path
    )

    assert session.sweep_expired(admission, idle_s=60.0, max_life_s=0) == []
    assert session.count() == 1


async def test_sweep_is_idempotent_after_remove(tmp_path):
    import agent.control.channel as ch

    admission = _admission()
    await ch._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    session.get("s1").last_active_at -= 10_000

    assert session.remove("s1", admission) is True
    assert admission.running_count == 0
    # A sweep after the session is already gone must not double-release the slot.
    assert session.sweep_expired(admission, idle_s=1.0, max_life_s=0) == []
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
        ws, {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 1 AS n"}, tmp_path
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
                ws, {"session_id": "s1", "query_id": "stmt1", "sql": "SELECT 1"}, tmp_path
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
        ws, {"session_id": "missing", "query_id": "x", "sql": "SELECT 1"}, tmp_path
    )
    frames = _frames(ws)
    assert [f.type for f in frames] == [FrameType.STATEMENT_ACK, FrameType.QUERY_DONE]
    assert frames[1].payload["status"] == "failed"


def test_capabilities_advertise_statement_ack():
    """The API gates the short ack deadline on this feature; without it an older
    agent's statements would all be reaped as undelivered."""
    import agent.control.channel as ch

    assert "statement_ack" in ch._PROTOCOL_FEATURES
