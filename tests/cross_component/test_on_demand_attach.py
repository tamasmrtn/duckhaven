"""On-demand catalog attach end to end: real API, agent, Postgres, Polaris and object store.

A workspace binds several DuckLake catalogs. What a statement attaches is read
from the outside: each attached DuckLake catalog holds one Postgres connection
under that catalog's own role for as long as the DuckDB connection is open, so
a held SQL session shows exactly which catalogs it has attached.

Skipped unless the harness is pointed at a DuckLake catalog database.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.cross_component

if not os.getenv("DUCKLAKE_DATABASE_URL"):
    pytest.skip("DUCKLAKE_DATABASE_URL not set", allow_module_level=True)

_LAKES = 4


async def _poll(api_client, query_id: str) -> dict:
    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        body = (await api_client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.25)
    raise AssertionError(f"query {query_id} did not finish in time")


async def _run(api_client, workspace: str, agent_id: str, sql: str) -> dict:
    created = await api_client.post(
        f"/api/workspaces/{workspace}/queries",
        json={"sql": sql, "agent_id": agent_id, "use_cache": False},
    )
    assert created.status_code == 202, created.text
    return await _poll(api_client, created.json()["id"])


async def _rows(api_client, query: dict) -> list[dict]:
    assert query["status"] == "done", query
    return (await api_client.get(f"/api/queries/{query['id']}/rows")).json()["rows"]


@pytest_asyncio.fixture
async def lakes(api_client, workspace, healthy_agent):
    """`_LAKES` DuckLake catalogs bound to `workspace`, each holding `analytics.t`.

    Catalogs outlive their workspace, so they are dropped on teardown."""
    slugs = [f"odl_{uuid.uuid4().hex[:6]}_{i}" for i in range(_LAKES)]
    for i, slug in enumerate(slugs):
        resp = await api_client.post(
            f"/api/workspaces/{workspace}/catalogs", json={"name": slug, "kind": "ducklake"}
        )
        assert resp.status_code == 201, resp.text
        made = await _run(
            api_client,
            workspace,
            healthy_agent["id"],
            f"CREATE TABLE {slug}.analytics.t AS SELECT {i} AS lake, range AS i FROM range(10)",
        )
        assert made["status"] == "done", made
    yield slugs

    listed = (await api_client.get("/api/catalogs")).json()
    for slug in slugs:
        await api_client.delete(f"/api/workspaces/{workspace}/catalogs/{slug}")
        match = next((c for c in listed if c["slug"] == slug), None)
        if match is not None:
            await api_client.delete(f"/api/catalogs/{match['id']}")


async def _connections(slugs: list[str]) -> dict[str, int]:
    """Postgres connections held under each catalog's role, by slug."""
    import asyncpg

    url = os.environ["DUCKLAKE_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT usename, count(*) AS n FROM pg_stat_activity "
            "WHERE usename = ANY($1::text[]) GROUP BY usename",
            [f"dl_{s}" for s in slugs],
        )
    finally:
        await conn.close()
    held = {r["usename"]: r["n"] for r in rows}
    return {s: held.get(f"dl_{s}", 0) for s in slugs}


async def _open_session(api_client, workspace: str, agent_id: str) -> str:
    resp = await api_client.post(
        f"/api/workspaces/{workspace}/sql/sessions", json={"agent_id": agent_id}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _exec(api_client, session_id: str, sql: str) -> dict:
    created = await api_client.post(f"/api/sql/sessions/{session_id}/statements", json={"sql": sql})
    assert created.status_code in (200, 202), created.text
    return await _poll(api_client, created.json()["id"])


async def test_a_session_attaches_only_what_its_statements_name(
    api_client, workspace, healthy_agent, lakes
) -> None:
    """Opening attaches the active catalog alone; each statement adds what it names;
    `USE` into a catalog not yet attached works."""
    session_id = await _open_session(api_client, workspace, healthy_agent["id"])
    try:
        assert await _connections(lakes) == dict.fromkeys(lakes, 0)

        named = await _exec(
            api_client, session_id, f"SELECT count(*) AS n FROM {lakes[0]}.analytics.t"
        )
        assert await _rows(api_client, named) == [{"n": 10}]
        held = await _connections(lakes)
        assert held[lakes[0]] == 1
        assert sum(held.values()) == 1, held

        used = await _exec(api_client, session_id, f"USE {lakes[1]}.analytics")
        assert used["status"] == "done", used
        unqualified = await _exec(api_client, session_id, "SELECT max(lake) AS lake FROM t")
        assert await _rows(api_client, unqualified) == [{"lake": 1}]
        held = await _connections(lakes)
        assert held[lakes[2]] == held[lakes[3]] == 0, held
    finally:
        await api_client.delete(f"/api/sql/sessions/{session_id}")


async def test_a_join_across_catalogs_attaches_both(
    api_client, workspace, healthy_agent, lakes
) -> None:
    joined = await _run(
        api_client,
        workspace,
        healthy_agent["id"],
        f"SELECT count(*) AS n FROM {lakes[0]}.analytics.t a "
        f"JOIN {lakes[3]}.analytics.t b USING (i)",
    )
    assert await _rows(api_client, joined) == [{"n": 10}]


async def test_a_view_reaches_a_catalog_the_query_never_names(
    api_client, workspace, healthy_agent, lakes
) -> None:
    """The agent fetches the view's catalog from the control plane mid-statement."""
    agent_id = healthy_agent["id"]
    view = f"{lakes[0]}.analytics.v_other"
    made = await _run(
        api_client,
        workspace,
        agent_id,
        f"CREATE VIEW {view} AS SELECT max(lake) AS lake FROM {lakes[2]}.analytics.t",
    )
    assert made["status"] == "done", made

    read = await _run(api_client, workspace, agent_id, f"SELECT lake FROM {view}")
    assert await _rows(api_client, read) == [{"lake": 2}]


async def test_a_listing_sees_every_catalog(api_client, workspace, healthy_agent, lakes) -> None:
    listed = await _run(
        api_client,
        workspace,
        healthy_agent["id"],
        "SELECT database_name FROM duckdb_databases() ORDER BY database_name",
    )
    names = {r["database_name"] for r in await _rows(api_client, listed)}
    assert set(lakes) <= names
