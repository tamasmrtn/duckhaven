"""On-demand attach over the control channel.

Sessions open with real in-memory DuckDB. A catalog "attach" is an in-memory
database holding `main.t`, and a fake socket answers CATALOG_REQUEST the way the
control plane would.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.control import session
from agent.executor import runner
from agent.executor.admission import Admission
from duckhaven_shared.protocol import Frame, FrameType


def _admission(profile: str = "single") -> Admission:
    return Admission(
        profile=profile,
        headroom=0.0,
        mem_bytes_provider=lambda: 1024**3,
        cores_provider=lambda: 2,
    )


class _ControlPlane:
    """A socket whose far end answers catalog requests from `grants`."""

    def __init__(self, grants: dict[str, dict | None] | None = None) -> None:
        self.sent: list[Frame] = []
        self.grants = grants or {}

    async def send(self, msg: str) -> None:
        import agent.control.channel as ch

        frame = Frame.model_validate_json(msg)
        self.sent.append(frame)
        if frame.type == FrameType.CATALOG_REQUEST:
            slug = frame.payload["catalog"]
            answer = {"request_id": frame.payload["request_id"], "catalog": self.grants.get(slug)}
            asyncio.get_running_loop().call_soon(ch._resolve_catalog_request, answer)

    def requests(self) -> list[str]:
        return [f.payload["catalog"] for f in self.sent if f.type == FrameType.CATALOG_REQUEST]

    def done(self) -> Frame:
        return next(f for f in reversed(self.sent) if f.type == FrameType.QUERY_DONE)


def _descriptor(slug: str) -> dict:
    return {"slug": slug, "kind": "ducklake", "backend": {"kind": "object_store"}}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    session._sessions.clear()
    attached: list[str] = []

    def fake_attach_one(conn, cat, endpoint):
        attached.append(cat["slug"])
        conn.execute(f"ATTACH ':memory:' AS {cat['slug']}")
        conn.execute(f"CREATE TABLE {cat['slug']}.main.t AS SELECT 42 AS v")

    monkeypatch.setattr(runner, "_attach_one", fake_attach_one)
    yield attached
    session._sessions.clear()


async def _open(admission, **payload) -> session.SessionState:
    import agent.control.channel as ch

    await ch._handle_open_session(_ControlPlane(), {"session_id": "s1", **payload}, admission)
    state = session.get("s1")
    assert state is not None
    return state


def _with_view_over(state: session.SessionState, slug: str) -> None:
    """A view reading `slug`, which is then detached: the shape the control
    plane cannot see, because statements name only the view."""
    state.conn.execute(f"ATTACH ':memory:' AS {slug}")
    state.conn.execute(f"CREATE TABLE {slug}.main.t AS SELECT 42 AS v")
    state.conn.execute(f"CREATE VIEW v AS SELECT * FROM {slug}.main.t")
    state.conn.execute(f"DETACH {slug}")


async def _exec(ws, admission, tmp_path, sql, **payload) -> Frame:
    import agent.control.channel as ch

    await ch._handle_exec_statement(
        ws, {"session_id": "s1", "query_id": "q1", "sql": sql, **payload}, tmp_path, admission
    )
    return ws.done()


def test_capabilities_advertise_on_demand_attach():
    """The control plane only trims what it attaches for agents that say so."""
    import agent.control.channel as ch

    assert "on_demand_attach" in ch._PROTOCOL_FEATURES


async def test_the_estimate_key_is_the_workspace_not_what_is_attached_so_far():
    state = await _open(_admission(), workspace_catalogs=["lake", "other"])
    assert state.catalogs == frozenset({"lake", "other"})


async def test_a_statement_attaches_the_catalogs_it_names_first(tmp_path, _isolated):
    admission = _admission()
    await _open(admission, workspace_catalogs=["other"])

    ws = _ControlPlane()
    done = await _exec(
        ws, admission, tmp_path, "SELECT v FROM other.main.t", catalogs=[_descriptor("other")]
    )
    assert done.payload["status"] == "done", done.payload
    # Sent again with the next statement, and already there.
    done = await _exec(
        _ControlPlane(), admission, tmp_path, "SELECT 1", catalogs=[_descriptor("other")]
    )
    assert done.payload["status"] == "done"
    assert _isolated == ["other"]
    assert ws.requests() == []


async def test_a_catalog_only_a_view_reads_is_requested_and_attached(tmp_path, _isolated):
    admission = _admission()
    state = await _open(admission, workspace_catalogs=["other"])
    _with_view_over(state, "other")

    ws = _ControlPlane(grants={"other": _descriptor("other")})
    done = await _exec(ws, admission, tmp_path, "SELECT v FROM v")

    assert done.payload["status"] == "done", done.payload
    assert ws.requests() == ["other"]
    assert _isolated == ["other"]


async def test_a_refused_catalog_fails_the_statement_with_duckdbs_error(tmp_path):
    admission = _admission()
    state = await _open(admission, workspace_catalogs=["other"])
    _with_view_over(state, "other")

    ws = _ControlPlane(grants={"other": None})
    done = await _exec(ws, admission, tmp_path, "SELECT v FROM v")

    assert done.payload["status"] == "failed"
    assert 'Catalog "other" does not exist' in done.payload["error"]
    assert ws.requests() == ["other"]


async def test_a_name_outside_the_workspace_is_never_requested(tmp_path):
    admission = _admission()
    state = await _open(admission, workspace_catalogs=["lake"])
    _with_view_over(state, "other")

    ws = _ControlPlane()
    done = await _exec(ws, admission, tmp_path, "SELECT v FROM v")

    assert done.payload["status"] == "failed"
    assert ws.requests() == []


async def test_an_older_control_plane_is_never_asked(tmp_path):
    """Without `workspace_catalogs` the control plane attached everything at open
    and has no handler for the request."""
    admission = _admission()
    state = await _open(admission)
    _with_view_over(state, "other")

    ws = _ControlPlane()
    done = await _exec(ws, admission, tmp_path, "SELECT v FROM v")

    assert done.payload["status"] == "failed"
    assert ws.requests() == []


@pytest.mark.parametrize("on_demand", [True, False])
async def test_one_shot_dispatch_hands_the_runner_what_it_needs(tmp_path, monkeypatch, on_demand):
    import agent.control.channel as ch

    captured: dict = {}

    async def fake_run_query(sql, result_path, timeout_s, **kwargs):
        captured.update(kwargs)
        return {"row_count": 0, "duration_ms": 0, "wrote_result": False, "profile": None}

    monkeypatch.setattr(ch, "run_query", fake_run_query)
    payload: dict = {"query_id": "q", "sql": "SELECT 1", "catalogs": []}
    if on_demand:
        payload["workspace_catalogs"] = ["lake"]
        payload["preload"] = {"catalog_kinds": ["ducklake"], "backend_kinds": ["object_store"]}

    await ch._handle_dispatch(_ControlPlane(), payload, tmp_path, _admission())

    assert (captured["attach_missing"] is not None) is on_demand
    assert captured["preload_catalog_kinds"] == (["ducklake"] if on_demand else [])
    assert captured["preload_backend_kinds"] == (["object_store"] if on_demand else [])


async def test_an_unanswered_request_gives_up(monkeypatch):
    import agent.control.channel as ch

    monkeypatch.setattr(ch, "_CATALOG_REQUEST_TIMEOUT_S", 0.05)

    class _Silent:
        async def send(self, msg: str) -> None:
            pass

    assert await ch._request_catalog(_Silent(), "q", "other") is None
    assert ch._catalog_requests == {}
