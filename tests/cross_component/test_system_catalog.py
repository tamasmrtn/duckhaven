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


@pytest.mark.parametrize(
    "table",
    [
        "info_schema.catalogs",
        "info_schema.schemata",
        "info_schema.tables",
        "info_schema.columns",
        "info_schema.routines",
        "info_schema.semantic_models",
        "info_schema.semantic_datasets",
        "info_schema.semantic_dimensions",
        "info_schema.semantic_metrics",
        "info_schema.semantic_relationships",
        "access.audit",
        "access.grants",
        "access.users",
        "access.workspaces",
        "access.credentials",
        "access.table_access",
        "query.history",
        "query.sessions",
        "lineage.table_lineage",
        "lineage.column_lineage",
        "jobs.schedules",
        "compute.agents",
        "compute.agent_events",
        "compute.agent_utilization",
        "storage.table_health",
        "storage.maintenance_operations",
        "storage.backends",
        "ai.assistant_usage",
        "ai.tool_calls",
    ],
)
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


@pytest_asyncio.fixture
async def member(stack, api_client, workspace):
    """A client signed in as a reader of `workspace` with no system permission, and
    the reader's id."""
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
    async with httpx.AsyncClient(base_url=stack.base_url, timeout=30.0) as client:
        login = await client.post("/api/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        yield client, user.json()["id"]


async def test_a_member_without_the_permission_is_refused(member, workspace, ready) -> None:
    client, _ = member
    resp = await _submit(client, workspace, ready, "SELECT * FROM duckhaven.access.audit;")

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


async def test_a_member_sees_a_new_table_in_info_schema(
    api_client, member, workspace, catalog, ready
) -> None:
    """The statement that made it has it read in within a tick or two."""
    client, _ = member
    created = await _submit(
        api_client,
        workspace,
        ready,
        f"CREATE TABLE {catalog}.analytics.xc_info AS SELECT 1::BIGINT AS id, 'a' AS name;",
    )
    assert created.status_code == 202, created.text
    assert (await _finished(api_client, created.json()["id"]))["status"] == "done"

    where = f"WHERE catalog_name = '{catalog}' AND table_name = 'xc_info'"
    sql = f"SELECT schema_name, column_count FROM duckhaven.info_schema.tables {where};"
    deadline = asyncio.get_event_loop().time() + _DEADLINE_S
    while not (rows := await _rows(client, workspace, ready, sql)):
        assert asyncio.get_event_loop().time() < deadline, "the table never reached info_schema"
        await asyncio.sleep(2.0)

    assert rows == [{"schema_name": "analytics", "column_count": 2}]
    columns = await _rows(
        client,
        workspace,
        ready,
        "SELECT column_name, data_type FROM duckhaven.info_schema.columns "
        f"{where} ORDER BY ordinal_position;",
    )
    assert columns == [
        {"column_name": "id", "data_type": "BIGINT"},
        {"column_name": "name", "data_type": "VARCHAR"},
    ]


async def _until(client, workspace: str, agent: str, sql: str, expected: list[dict]) -> None:
    deadline = asyncio.get_event_loop().time() + _DEADLINE_S
    while (rows := await _rows(client, workspace, agent, sql)) != expected:
        assert asyncio.get_event_loop().time() < deadline, rows
        await asyncio.sleep(2.0)


async def test_a_scoped_catalog_shows_a_member_only_what_a_grant_covers(
    api_client, member, workspace, catalog, ready
) -> None:
    client, member_id = member
    for name in ("xc_granted", "xc_hidden"):
        created = await _submit(
            api_client,
            workspace,
            ready,
            f"CREATE TABLE {catalog}.analytics.{name} AS SELECT 1 AS id;",
        )
        assert created.status_code == 202, created.text
        assert (await _finished(api_client, created.json()["id"]))["status"] == "done"
    tables = (
        "SELECT table_name FROM duckhaven.info_schema.tables "
        f"WHERE catalog_name = '{catalog}' ORDER BY table_name;"
    )
    both = [{"table_name": "xc_granted"}, {"table_name": "xc_hidden"}]
    await _until(client, workspace, ready, tables, both)

    base = f"/api/workspaces/{workspace}/catalogs/{catalog}"
    scoped = await api_client.patch(f"{base}/access-mode", json={"access_mode": "scoped"})
    assert scoped.status_code == 200, scoped.text
    granted = await api_client.put(
        f"{base}/grants",
        json={
            "user_id": member_id,
            "schema_name": "analytics",
            "table_name": "xc_granted",
            "tier": "reader",
        },
    )
    assert granted.status_code == 201, granted.text

    await _until(client, workspace, ready, tables, [{"table_name": "xc_granted"}])
    columns = (
        "SELECT DISTINCT table_name FROM duckhaven.info_schema.columns "
        f"WHERE catalog_name = '{catalog}';"
    )
    assert await _rows(client, workspace, ready, columns) == [{"table_name": "xc_granted"}]


async def test_a_query_s_table_access_is_recorded(api_client, workspace, catalog, ready) -> None:
    created = await _submit(
        api_client,
        workspace,
        ready,
        f"CREATE TABLE {catalog}.analytics.xc_access AS SELECT 1 AS id;",
    )
    assert created.status_code == 202, created.text
    assert (await _finished(api_client, created.json()["id"]))["status"] == "done"
    # Unqualified: resolved where the query ran.
    read = await api_client.post(
        f"/api/workspaces/{workspace}/queries",
        json={
            "sql": "SELECT count(*) FROM xc_access;",
            "agent_id": ready,
            "catalog": catalog,
            "schema": "analytics",
            "use_cache": False,
        },
    )
    assert read.status_code == 202, read.text
    query_id = read.json()["id"]
    assert (await _finished(api_client, query_id))["status"] == "done"

    sql = (
        "SELECT catalog_name, schema_name, table_name, access FROM duckhaven.access.table_access "
        f"WHERE query_id = '{query_id}';"
    )
    expected = [
        {
            "catalog_name": catalog,
            "schema_name": "analytics",
            "table_name": "xc_access",
            "access": "read",
        }
    ]
    await _until(api_client, workspace, ready, sql, expected)


async def test_the_agent_s_connection_reaches_compute(api_client, workspace, ready) -> None:
    sql = (
        "SELECT count(*) > 0 AS connected FROM duckhaven.compute.agent_events "
        f"WHERE agent_id = '{ready}' AND event = 'connected';"
    )
    await _until(api_client, workspace, ready, sql, [{"connected": True}])
