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


async def test_paginate_thousand_rows(api_client, workspace, healthy_agent) -> None:
    """A 1000-row result pages 100 at a time into exactly 10 pages.

    Exercises the real keystone path end to end: the agent materializes 1000 rows
    to Parquet, and each page is fetched as its own row window (row_offset/
    row_limit) the control plane proxies from the agent — never the whole file.
    The concatenated pages must reproduce 0..999 in order, proving correct
    windowing across the cursor boundaries.
    """
    body = await _run(
        api_client,
        workspace,
        healthy_agent["id"],
        "SELECT n FROM range(1000) t(n) ORDER BY n",
    )
    assert body["status"] == "done", body
    assert body["row_count"] == 1000

    query_id = body["id"]
    pages: list[dict] = []
    collected: list[int] = []
    cursor: str | None = None
    while True:
        url = f"/api/queries/{query_id}/rows?limit=100"
        if cursor is not None:
            url += f"&cursor={cursor}"
        page = (await api_client.get(url)).json()
        assert page["columns"] == ["n"]
        assert page["total"] == 1000
        pages.append(page)
        collected.extend(r["n"] for r in page["rows"])
        cursor = page["cursor"]
        if cursor is None:
            break
        assert len(pages) < 20, "cursor never terminated"

    assert len(pages) == 10, f"expected 10 pages, got {len(pages)}"
    assert all(len(p["rows"]) == 100 for p in pages)
    assert pages[-1]["cursor"] is None
    assert collected == list(range(1000))


async def test_sql_metadata_from_live_agent(api_client, workspace, healthy_agent) -> None:
    """The editor's autocomplete dictionary is sourced from the real agent.

    Runs duckdb_functions()/duckdb_keywords()/duckdb_types() end to end and
    asserts the shaped response is non-empty and well-formed.
    """
    resp = await api_client.get(f"/api/workspaces/{workspace}/sql-metadata")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["functions"], "expected DuckDB functions"
    assert body["keywords"], "expected DuckDB keywords"
    assert body["types"], "expected DuckDB types"

    names = {f["name"] for f in body["functions"]}
    assert {"count", "abs"} <= names
    count = next(f for f in body["functions"] if f["name"] == "count")
    assert count["signature"].startswith("count(")
    assert {k["name"] for k in body["keywords"]} & {"select", "from", "where"}


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
