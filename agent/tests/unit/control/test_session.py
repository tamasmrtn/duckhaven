"""Held SQL-session lifecycle on the agent: OPEN_SESSION holds a connection +
admission slot, EXEC_STATEMENT runs on the held connection without closing it,
CLOSE_SESSION / reconnect release the slot."""

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
