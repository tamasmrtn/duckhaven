"""The result cache across a real API, a real agent, Polaris and DuckLake.

The property everything here checks: a repeated read is answered without
running, and a cached answer is never stale -- not after a write through
DuckHaven, not after a schema change, and not after a write by an engine
DuckHaven never hears from.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import duckdb
import pytest
from testkit import polaris as dh_polaris
from testkit.iceberg import attach_catalog

pytestmark = pytest.mark.cross_component


async def _wait(api_client, query_id: str) -> dict:
    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        body = (await api_client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"query {query_id} did not finish in time")


async def _run(api_client, workspace: str, agent_id: str, sql: str, **extra) -> dict:
    created = await api_client.post(
        f"/api/workspaces/{workspace}/queries", json={"sql": sql, "agent_id": agent_id, **extra}
    )
    assert created.status_code == 202, created.text
    body = await _wait(api_client, created.json()["id"])
    assert body["status"] == "done", body
    return body


async def _rows(api_client, query_id: str) -> list[dict]:
    return (await api_client.get(f"/api/queries/{query_id}/rows")).json()["rows"]


async def _until_hit(api_client, workspace: str, agent_id: str, sql: str) -> dict:
    """Admission runs just after a miss finishes; retry briefly until it lands."""
    for _ in range(20):
        body = await _run(api_client, workspace, agent_id, sql)
        if body["cache_status"] == "hit":
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"never served from the cache: {body}")


async def _table(api_client, workspace: str, agent_id: str, catalog: str) -> str:
    name = f"rc_{uuid.uuid4().hex[:8]}"
    await _run(
        api_client,
        workspace,
        agent_id,
        f"CREATE TABLE {catalog}.analytics.{name} AS SELECT i AS id FROM range(10) r(i)",
    )
    return name


async def test_a_repeated_read_is_served_until_its_table_changes(
    api_client, workspace, catalog, healthy_agent
) -> None:
    agent = healthy_agent["id"]
    table = await _table(api_client, workspace, agent, catalog)
    sql = f"SELECT count(*) AS n FROM {catalog}.analytics.{table}"

    first = await _run(api_client, workspace, agent, sql)
    assert first["cache_status"] == "miss"
    hit = await _until_hit(api_client, workspace, agent, sql)
    assert hit["agent_id"] is None
    assert await _rows(api_client, hit["id"]) == [{"n": 10}]

    await _run(api_client, workspace, agent, f"INSERT INTO {catalog}.analytics.{table} VALUES (99)")
    after = await _run(api_client, workspace, agent, sql)
    assert after["cache_status"] == "miss"
    assert await _rows(api_client, after["id"]) == [{"n": 11}]


async def test_a_write_by_another_engine_is_never_served_stale(
    api_client, workspace, catalog, healthy_agent
) -> None:
    """The cache cannot hear an external commit; it asks Polaris instead."""
    agent = healthy_agent["id"]
    table = await _table(api_client, workspace, agent, catalog)
    sql = f"SELECT count(*) AS n FROM {catalog}.analytics.{table}"
    await _run(api_client, workspace, agent, sql)
    await _until_hit(api_client, workspace, agent, sql)

    catalogs = (await api_client.get("/api/catalogs")).json()
    polaris_name = next(c["polaris_name"] for c in catalogs if c["slug"] == catalog)
    external = duckdb.connect()
    try:
        attach_catalog(
            external,
            os.environ["POLARIS_BASE_URL"],
            polaris_name,
            "analytics",
            dh_polaris.env_creds(),
        )
        external.execute(f"INSERT INTO {table} VALUES (100), (101)")
    finally:
        external.close()

    after = await _run(api_client, workspace, agent, sql)
    assert after["cache_status"] == "miss"
    assert await _rows(api_client, after["id"]) == [{"n": 12}]


async def test_a_schema_change_is_never_served_stale(
    api_client, workspace, catalog, healthy_agent
) -> None:
    agent = healthy_agent["id"]
    table = await _table(api_client, workspace, agent, catalog)
    sql = f"SELECT * FROM {catalog}.analytics.{table} ORDER BY id LIMIT 1"
    await _run(api_client, workspace, agent, sql)
    await _until_hit(api_client, workspace, agent, sql)

    await _run(
        api_client,
        workspace,
        agent,
        f"ALTER TABLE {catalog}.analytics.{table} ADD COLUMN extra INTEGER",
    )
    after = await _run(api_client, workspace, agent, sql)
    assert after["cache_status"] == "miss"
    assert await _rows(api_client, after["id"]) == [{"id": 0, "extra": None}]


async def test_volatile_and_opted_out_runs_execute(
    api_client, workspace, catalog, healthy_agent
) -> None:
    agent = healthy_agent["id"]
    volatile = await _run(api_client, workspace, agent, "SELECT now() AS t")
    assert (volatile["cache_status"], volatile["cache_detail"]) == (
        "ineligible",
        "volatile_function",
    )
    table = await _table(api_client, workspace, agent, catalog)
    sql = f"SELECT count(*) AS n FROM {catalog}.analytics.{table}"
    await _run(api_client, workspace, agent, sql)
    await _until_hit(api_client, workspace, agent, sql)
    forced = await _run(api_client, workspace, agent, sql, use_cache=False)
    assert (forced["cache_status"], forced["cache_detail"]) == ("bypass", "opted_out")
    assert forced["agent_id"] == agent


async def test_a_small_result_is_served_after_its_agent_stops(
    api_client, workspace, catalog, healthy_agent, spawn_agent
) -> None:
    """Small results are copied into the control plane: no agent is needed."""
    table = await _table(api_client, workspace, healthy_agent["id"], catalog)
    sql = f"SELECT count(*) AS n FROM {catalog}.analytics.{table}"
    known = {a["id"] for a in (await api_client.get("/api/agents")).json()}
    proc = spawn_agent()
    extra = None
    # An agent is healthy as soon as it authenticates, but its extensions arrive in
    # a later AGENT_STATUS frame; dispatching before then is agent_incompatible.
    for _ in range(120):
        agents = (await api_client.get("/api/agents")).json()
        extra = next(
            (
                a
                for a in agents
                if a["id"] not in known
                and a.get("status") == "healthy"
                and "httpfs" in (a.get("capabilities") or {}).get("extensions", [])
            ),
            None,
        )
        if extra is not None:
            break
        await asyncio.sleep(0.5)
    assert extra is not None, "the extra agent never came up"

    await _run(api_client, workspace, extra["id"], sql)
    await _until_hit(api_client, workspace, extra["id"], sql)
    proc.terminate()
    proc.wait(timeout=15)

    hit = await _run(api_client, workspace, extra["id"], sql)
    assert hit["cache_status"] == "hit"
    assert await _rows(api_client, hit["id"]) == [{"n": 10}]


# --- SQL sessions --------------------------------------------------------------


async def _statement(api_client, session_id: str, sql: str) -> dict:
    created = await api_client.post(f"/api/sql/sessions/{session_id}/statements", json={"sql": sql})
    assert created.status_code in (200, 202), created.text
    return await _wait(api_client, created.json()["id"])


async def test_session_reads_use_the_cache_only_outside_transactions(
    api_client, workspace, catalog, healthy_agent
) -> None:
    agent = healthy_agent["id"]
    table = await _table(api_client, workspace, agent, catalog)
    sql = f"SELECT count(*) AS n FROM {catalog}.analytics.{table}"
    opened = await api_client.post(
        f"/api/workspaces/{workspace}/sql/sessions", json={"agent_id": agent}
    )
    assert opened.status_code == 201, opened.text
    session = opened.json()["id"]
    try:
        await _statement(api_client, session, sql)
        for _ in range(20):
            again = await _statement(api_client, session, sql)
            if again["cache_status"] == "hit":
                break
            await asyncio.sleep(0.3)
        assert again["cache_status"] == "hit"

        await _statement(api_client, session, "BEGIN")
        inside = await _statement(api_client, session, sql)
        assert (inside["cache_status"], inside["cache_detail"]) == ("ineligible", "in_transaction")
        await _statement(api_client, session, "COMMIT")

        await _statement(api_client, session, "CREATE TEMP TABLE scratch AS SELECT 1 AS x")
        tainted = await _statement(api_client, session, sql)
        assert (tainted["cache_status"], tainted["cache_detail"]) == (
            "ineligible",
            "session_state",
        )
    finally:
        await api_client.delete(f"/api/sql/sessions/{session}")


# --- DuckLake ------------------------------------------------------------------


@pytest.mark.skipif(not os.getenv("DUCKLAKE_DATABASE_URL"), reason="DUCKLAKE_DATABASE_URL not set")
async def test_ducklake_reads_are_cached_until_the_table_changes(
    api_client, workspace, healthy_agent
) -> None:
    agent = healthy_agent["id"]
    slug = f"rc_{uuid.uuid4().hex[:8]}"
    created = await api_client.post(
        f"/api/workspaces/{workspace}/catalogs", json={"name": slug, "kind": "ducklake"}
    )
    assert created.status_code == 201, created.text
    try:
        await _run(
            api_client,
            workspace,
            agent,
            f"CREATE TABLE {slug}.analytics.t AS SELECT i AS id FROM range(5) r(i)",
        )
        sql = f"SELECT count(*) AS n FROM {slug}.analytics.t"
        await _run(api_client, workspace, agent, sql)
        hit = await _until_hit(api_client, workspace, agent, sql)
        assert await _rows(api_client, hit["id"]) == [{"n": 5}]

        # Small enough to be inlined in the catalog database: still a change.
        await _run(api_client, workspace, agent, f"INSERT INTO {slug}.analytics.t VALUES (7)")
        after = await _run(api_client, workspace, agent, sql)
        assert after["cache_status"] == "miss"
        assert await _rows(api_client, after["id"]) == [{"n": 6}]
    finally:
        listed = (await api_client.get("/api/catalogs")).json()
        match = next((c for c in listed if c["slug"] == slug), None)
        if match is not None:
            await api_client.delete(f"/api/workspaces/{workspace}/catalogs/{slug}")
            await api_client.delete(f"/api/catalogs/{match['id']}")
