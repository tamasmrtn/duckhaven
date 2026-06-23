"""The built-in system catalog across the live stack.

Proves that, against real API + agent + Polaris + MinIO:
- the `duckhaven` catalog is attached to every workspace and its
  `query.history` becomes SQL-queryable once the materializer has run, and
- it is read-only — writes/DDL against it are rejected by the engine.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.cross_component


async def _run(api_client, workspace: str, agent_id: str, sql: str, timeout: float = 60.0) -> dict:
    created = await api_client.post(
        f"/api/workspaces/{workspace}/queries", json={"sql": sql, "agent_id": agent_id}
    )
    assert created.status_code == 202, created.text
    query_id = created.json()["id"]
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        body = (await api_client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"query {query_id} did not finish in time")


async def test_query_history_is_queryable(api_client, workspace, healthy_agent) -> None:
    """After running a query, it shows up in duckhaven.query.history within a few
    materializer cycles."""
    agent_id = healthy_agent["id"]
    # A marker query, so there is at least one terminal row to materialize.
    marker = await _run(api_client, workspace, agent_id, "SELECT 42 AS marker")
    assert marker["status"] == "done", marker

    deadline = asyncio.get_event_loop().time() + 45.0
    rows: list[dict] = []
    while asyncio.get_event_loop().time() < deadline:
        body = await _run(
            api_client,
            workspace,
            agent_id,
            "SELECT query_id, workspace_slug, statement_type, status "
            "FROM duckhaven.query.history WHERE statement_type = 'SELECT'",
        )
        if body["status"] == "done" and body["row_count"]:
            rows = (await api_client.get(f"/api/queries/{body['id']}/rows")).json()["rows"]
            break
        # Table not created yet (no cycle has appended) → retry.
        await asyncio.sleep(2.0)

    assert rows, "expected the marker query to appear in duckhaven.query.history"
    assert {"query_id", "workspace_slug", "statement_type", "status"} <= set(rows[0])


async def test_system_catalog_is_read_only(api_client, workspace, healthy_agent) -> None:
    """Writes/DDL against the system catalog are rejected by the engine."""
    agent_id = healthy_agent["id"]
    body = await _run(
        api_client,
        workspace,
        agent_id,
        "CREATE TABLE duckhaven.query.should_fail (x INTEGER)",
    )
    assert body["status"] == "failed", body
    assert body.get("error")
