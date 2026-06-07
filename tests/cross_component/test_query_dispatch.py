"""Query dispatch → execution → result retrieval across API + agent.

The full keystone path: the API dispatches over the WS control channel, the
real agent executes against Polaris/MinIO, reports QUERY_DONE, and the API
proxies the Parquet result back through ``/queries/{id}/rows``.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.cross_component


async def _run(api_client, workspace: str, agent_id: str, sql: str) -> dict:
    """Submit a query and poll until it reaches a terminal state."""
    created = await api_client.post(
        f"/api/workspaces/{workspace}/queries", json={"sql": sql, "agent_id": agent_id}
    )
    assert created.status_code == 202, created.text
    query_id = created.json()["id"]

    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        body = (await api_client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"query {query_id} did not finish in time")


async def test_select_one_roundtrip(api_client, workspace, healthy_agent) -> None:
    body = await _run(api_client, workspace, healthy_agent["id"], "SELECT 1 AS n")
    assert body["status"] == "done", body
    assert body["row_count"] == 1

    rows = (await api_client.get(f"/api/queries/{body['id']}/rows")).json()
    assert rows["columns"] == ["n"]
    assert rows["rows"] == [{"n": 1}]
    assert rows["total"] == 1


async def test_table_write_then_read(api_client, workspace, healthy_agent) -> None:
    agent_id = healthy_agent["id"]
    # Create the table through the control plane (real Polaris DDL).
    created = await api_client.post(
        f"/api/workspaces/{workspace}/schemas/analytics/tables",
        json={
            "name": "kv",
            "columns": [
                {"name": "k", "type": "VARCHAR", "nullable": False},
                {"name": "v", "type": "BIGINT", "nullable": True},
            ],
        },
    )
    assert created.status_code == 201, created.text

    # Insert via the agent (a DML query produces no result file).
    insert = await _run(api_client, workspace, agent_id, "INSERT INTO kv VALUES ('a', 1), ('b', 2)")
    assert insert["status"] == "done", insert
    empty = (await api_client.get(f"/api/queries/{insert['id']}/rows")).json()
    assert empty["rows"] == []

    # Read it back through the agent + result proxy.
    select = await _run(api_client, workspace, agent_id, "SELECT k, v FROM kv ORDER BY k")
    assert select["status"] == "done", select
    rows = (await api_client.get(f"/api/queries/{select['id']}/rows")).json()
    assert rows["rows"] == [{"k": "a", "v": 1}, {"k": "b", "v": 2}]
