"""The `duckhaven` system catalog end to end: the API's copy of the audit log, read
in SQL by a real agent that attaches its database read-only, as the reader login.

Covers what the unit suites cannot: the store created by a live API, its login
used by a real agent's `postgres` extension, an access change reaching the copy,
and the gate in front of it on the one-shot and the session paths.

Skipped unless the harness is pointed at a system catalog database.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.cross_component

if not os.getenv("SYSTEM_CATALOG_DATABASE_URL"):
    pytest.skip("SYSTEM_CATALOG_DATABASE_URL not set", allow_module_level=True)

_DEADLINE_S = 60.0


async def _submit(client, workspace: str, agent_id: str, sql: str) -> httpx.Response:
    return await client.post(
        f"/api/workspaces/{workspace}/queries",
        json={"sql": sql, "agent_id": agent_id, "use_cache": False},
    )


async def _finished(client, query_id: str) -> dict:
    deadline = asyncio.get_event_loop().time() + _DEADLINE_S
    while asyncio.get_event_loop().time() < deadline:
        body = (await client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"query {query_id} did not finish in time")


async def _rows(client, workspace: str, agent_id: str, sql: str) -> list[dict]:
    resp = await _submit(client, workspace, agent_id, sql)
    assert resp.status_code == 202, resp.text
    done = await _finished(client, resp.json()["id"])
    assert done["status"] == "done", done
    return (await client.get(f"/api/queries/{done['id']}/rows")).json()["rows"]


@pytest_asyncio.fixture
async def ready(api_client, workspace, healthy_agent) -> str:
    """The agent's id, once the API has created the store (on its first publish)."""
    agent_id = healthy_agent["id"]
    deadline = asyncio.get_event_loop().time() + _DEADLINE_S
    while True:
        resp = await _submit(
            api_client, workspace, agent_id, "SELECT count(*) FROM duckhaven.access.grants;"
        )
        if resp.status_code == 202:
            await _finished(api_client, resp.json()["id"])
            return agent_id
        assert resp.json().get("error") == "system_catalog_unavailable", resp.text
        assert asyncio.get_event_loop().time() < deadline, "the store was never created"
        await asyncio.sleep(1.0)


@pytest.mark.parametrize("table", ["access.audit", "access.grants", "query.history"])
async def test_an_admin_reads_each_table(api_client, workspace, ready, table) -> None:
    rows = await _rows(
        api_client, workspace, ready, f"SELECT count(*) AS n FROM duckhaven.{table};"
    )
    assert len(rows) == 1


async def test_an_access_change_reaches_the_copy(api_client, workspace, ready) -> None:
    """Creating the workspace was audited; it reaches the copy within the publish interval."""
    sql = (
        "SELECT action, actor_email FROM duckhaven.access.audit "
        f"WHERE workspace_slug = '{workspace}' AND action = 'workspace.create';"
    )
    deadline = asyncio.get_event_loop().time() + _DEADLINE_S
    while not (rows := await _rows(api_client, workspace, ready, sql)):
        assert asyncio.get_event_loop().time() < deadline, "the event never reached the copy"
        await asyncio.sleep(2.0)
    assert rows == [{"action": "workspace.create", "actor_email": "admin@xc.test"}]


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE xc_audit_copy AS SELECT * FROM duckhaven.access.audit;",
        "DELETE FROM duckhaven.access.audit;",
        "SELECT rolname FROM duckhaven.pg_catalog.pg_roles;",
        "SELECT * FROM duckhaven._state.cursors;",
    ],
)
async def test_anything_but_reading_its_tables_is_refused(api_client, workspace, ready, sql):
    resp = await _submit(api_client, workspace, ready, sql)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "grant_denied"


async def test_a_member_without_the_permission_is_refused(
    stack, api_client, workspace, ready
) -> None:
    email, password = f"member-{uuid.uuid4().hex[:8]}@xc.test", "xc-password-123"
    user = await api_client.post(
        "/api/admin/users", json={"email": email, "name": "Member", "password": password}
    )
    assert user.status_code == 201, user.text
    added = await api_client.post(
        f"/api/workspaces/{workspace}/members",
        json={"user_id": user.json()["id"], "role": "reader"},
    )
    assert added.status_code == 201, added.text

    async with httpx.AsyncClient(base_url=stack.base_url, timeout=30.0) as member:
        login = await member.post("/api/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        resp = await _submit(member, workspace, ready, "SELECT * FROM duckhaven.access.audit;")

    assert resp.status_code == 403, resp.text
    assert (
        resp.json()["message"] == "Reading duckhaven.access.audit needs the audit:read permission."
    )


async def test_a_session_reads_it_and_never_moves_into_it(api_client, workspace, ready) -> None:
    opened = await api_client.post(
        f"/api/workspaces/{workspace}/sql/sessions", json={"agent_id": ready}
    )
    assert opened.status_code == 201, opened.text
    session = opened.json()["id"]
    statements = f"/api/sql/sessions/{session}/statements"
    try:
        read = await api_client.post(
            statements, json={"sql": "SELECT count(*) FROM duckhaven.access.audit;"}
        )
        assert read.status_code in (200, 202), read.text
        assert (await _finished(api_client, read.json()["id"]))["status"] == "done"

        # Attached now, but the next statement is checked again all the same.
        moved = await api_client.post(statements, json={"sql": "USE duckhaven.access;"})
        assert moved.status_code == 403, moved.text
    finally:
        await api_client.delete(f"/api/sql/sessions/{session}")
