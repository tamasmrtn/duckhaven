"""Post-execution profile capture + exposure across API + agent.

The agent runs in the default ``auto`` profile, so each dispatched query is
sized from its EXPLAIN estimate and profiled after execution. This asserts the
profile rides the QUERY_DONE frame, is persisted, and is served by the
dedicated ``GET /queries/{id}/profile`` endpoint — the keystone of Part 2.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.cross_component


async def _run(api_client, workspace: str, agent_id: str, sql: str) -> dict:
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


async def test_select_profile_persisted_and_served(api_client, workspace, healthy_agent) -> None:
    sql = (
        "SELECT g, count(*) c FROM (SELECT i % 100 g FROM range(200000) t(i)) GROUP BY g ORDER BY c"
    )
    body = await _run(api_client, workspace, healthy_agent["id"], sql)
    assert body["status"] == "done", body

    profile = (await api_client.get(f"/api/queries/{body['id']}/profile")).json()
    assert profile is not None, "expected a profile for a SELECT in auto mode"

    summary = profile["summary"]
    assert summary["latency_ms"] > 0
    assert {"peak_memory_bytes", "spill_bytes", "rows_returned"} <= summary.keys()

    # The waiting-versus-working metrics survive the agent -> API -> client trip.
    # blocked_thread_time_ms is parsed from DuckDB's profile; admission_wait_ms
    # is DuckHaven's own and is injected by the runner. Both were being
    # collected and dropped before reaching anything that could show them.
    assert {"blocked_thread_time_ms", "admission_wait_ms"} <= summary.keys()
    assert summary["blocked_thread_time_ms"] >= 0
    assert summary["admission_wait_ms"] >= 0

    # The operator tree includes a blocking GROUP BY with actual cardinalities.
    types, produced = [], []
    stack = [profile["tree"]]
    while stack:
        node = stack.pop()
        types.append(node["type"])
        if node["rows_produced"] is not None:
            produced.append(node["rows_produced"])
        stack.extend(node["children"])
    assert any("GROUP_BY" in t for t in types)
    assert 100 in produced  # 100 distinct groups actually produced


async def test_ddl_has_null_profile(api_client, workspace, catalog, healthy_agent) -> None:
    agent_id = healthy_agent["id"]
    created = await api_client.post(
        f"/api/workspaces/{workspace}/catalogs/{catalog}/schemas/analytics/tables",
        json={"name": "prof_ddl", "columns": [{"name": "k", "type": "BIGINT", "nullable": True}]},
    )
    assert created.status_code == 201, created.text

    # An INSERT (DML) carries no profile — the UI shows the no-profile state.
    body = await _run(api_client, workspace, agent_id, "INSERT INTO prof_ddl VALUES (1), (2)")
    assert body["status"] == "done", body
    profile = (await api_client.get(f"/api/queries/{body['id']}/profile")).json()
    assert profile is None


async def test_profile_endpoint_404_for_unknown_query(api_client) -> None:
    import uuid

    resp = await api_client.get(f"/api/queries/{uuid.uuid4()}/profile")
    assert resp.status_code == 404
