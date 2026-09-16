"""DuckLake end to end: real API, real agent, real Postgres, real object store.

The unit and integration suites cover the pieces. What this covers is the join
between them — that a catalog created through the HTTP API is attachable by an
agent that dialled home on its own, that DDL and DML dispatched over the WebSocket
actually commit, and that the control plane then reads that state back out of the
catalog database.

The last test is the one that matters most: an Iceberg catalog and a DuckLake
catalog attached to one workspace and joined in a single query. If that cannot be
written, catalog kind and storage backend are not the orthogonal axes this design
claims they are.

Skipped unless the harness is pointed at a DuckLake catalog database, so the
suite still runs against a Polaris-only stack.
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


@pytest_asyncio.fixture
async def slug(api_client, workspace):
    """A unique catalog name, whose catalog is dropped on teardown.

    Unlike a Polaris catalog, a DuckLake catalog's metadata schema lives in the
    catalog database and outlives the workspace — `delete_workspace` deliberately
    never touches catalog rows. Without this, every run would leave another
    `cat_*` schema behind, the same accumulation `workspace_factory` avoids for
    Polaris. Dropping also purges the catalog's object-storage prefix.
    """
    name = f"dl_{uuid.uuid4().hex[:8]}"
    yield name

    listed = await api_client.get("/api/catalogs")
    if listed.status_code != 200:
        return
    match = next((c for c in listed.json() if c["slug"] == name), None)
    if match is None:
        return
    await api_client.delete(f"/api/workspaces/{workspace}/catalogs/{name}")
    await api_client.delete(f"/api/catalogs/{match['id']}")


async def _make_ducklake(api_client, workspace: str, slug: str) -> dict:
    resp = await api_client.post(
        f"/api/workspaces/{workspace}/catalogs", json={"name": slug, "kind": "ducklake"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "ducklake"
    return body


async def test_create_query_and_read_back(api_client, workspace, healthy_agent, slug) -> None:
    """The keystone path, for the new kind."""
    await _make_ducklake(api_client, workspace, slug)
    agent_id = healthy_agent["id"]

    created = await _run(
        api_client,
        workspace,
        agent_id,
        f"CREATE TABLE {slug}.analytics.events AS SELECT i AS id, i * 2 AS n FROM range(5000) r(i)",
    )
    assert created["status"] == "done", created

    counted = await _run(
        api_client, workspace, agent_id, f"SELECT count(*) AS c FROM {slug}.analytics.events"
    )
    assert counted["status"] == "done", counted
    rows = (await api_client.get(f"/api/queries/{counted['id']}/rows")).json()
    assert rows["rows"] == [{"c": 5000}]

    # And the control plane sees the table without going anywhere near an agent.
    listed = await api_client.get(
        f"/api/workspaces/{workspace}/catalogs/{slug}/schemas/analytics/tables"
    )
    assert listed.status_code == 200, listed.text
    assert "events" in [t["name"] for t in listed.json()]


async def test_table_detail_describes_itself_as_ducklake(
    api_client, workspace, healthy_agent, slug
) -> None:
    await _make_ducklake(api_client, workspace, slug)
    await _run(
        api_client,
        workspace,
        healthy_agent["id"],
        f"CREATE TABLE {slug}.analytics.t AS SELECT 1 AS a, 'x' AS b",
    )

    got = await api_client.get(
        f"/api/workspaces/{workspace}/catalogs/{slug}/schemas/analytics/tables/t"
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["format"] == "DUCKLAKE"
    # An Iceberg concept with no DuckLake equivalent: null, not faked.
    assert body["format_version"] is None
    assert {c["name"] for c in body["columns"]} == {"a", "b"}


async def test_time_travel(api_client, workspace, healthy_agent, slug) -> None:
    await _make_ducklake(api_client, workspace, slug)
    agent_id = healthy_agent["id"]
    await _run(
        api_client,
        workspace,
        agent_id,
        f"CREATE TABLE {slug}.analytics.t AS SELECT i AS id FROM range(1000) r(i)",
    )
    snaps = await api_client.get(
        f"/api/workspaces/{workspace}/catalogs/{slug}/schemas/analytics/tables/t/snapshots"
    )
    assert snaps.status_code == 200, snaps.text
    listed = snaps.json()
    assert listed, "a table that was just written should have history"
    # Honest about what these are: a DuckLake snapshot is a catalog commit.
    assert listed[0]["granularity"] == "catalog"

    version = listed[0]["snapshot_id"]
    at = await _run(
        api_client,
        workspace,
        agent_id,
        f"SELECT count(*) AS c FROM {slug}.analytics.t AT (VERSION => {version})",
    )
    assert at["status"] == "done", at
    rows = (await api_client.get(f"/api/queries/{at['id']}/rows")).json()
    assert rows["rows"] == [{"c": 1000}]


async def test_schema_evolution(api_client, workspace, healthy_agent, slug) -> None:
    await _make_ducklake(api_client, workspace, slug)
    agent_id = healthy_agent["id"]
    await _run(api_client, workspace, agent_id, f"CREATE TABLE {slug}.analytics.t AS SELECT 1 AS a")
    added = await _run(
        api_client, workspace, agent_id, f"ALTER TABLE {slug}.analytics.t ADD COLUMN b VARCHAR"
    )
    assert added["status"] == "done", added

    got = await api_client.get(
        f"/api/workspaces/{workspace}/catalogs/{slug}/schemas/analytics/tables/t"
    )
    assert {c["name"] for c in got.json()["columns"]} == {"a", "b"}


async def test_structured_ddl_through_the_rest_surface(
    api_client, workspace, healthy_agent, slug
) -> None:
    """Create/drop schema and table via the catalog UI's endpoints, which for
    DuckLake are dispatched to an agent as SQL rather than sent to a REST
    catalog."""
    await _make_ducklake(api_client, workspace, slug)
    base = f"/api/workspaces/{workspace}/catalogs/{slug}/schemas"

    made = await api_client.post(base, json={"name": "staging"})
    assert made.status_code in (200, 201), made.text

    table = await api_client.post(
        f"{base}/staging/tables",
        json={"name": "orders", "columns": [{"name": "id", "type": "BIGINT", "nullable": False}]},
    )
    assert table.status_code in (200, 201), table.text
    assert table.json()["format"] == "DUCKLAKE"

    dropped = await api_client.delete(f"{base}/staging/tables/orders")
    assert dropped.status_code == 204, dropped.text
    assert "orders" not in [
        t["name"] for t in (await api_client.get(f"{base}/staging/tables")).json()
    ]


async def test_unsupported_column_type_is_refused_before_dispatch(
    api_client, workspace, healthy_agent, slug
) -> None:
    await _make_ducklake(api_client, workspace, slug)
    resp = await api_client.post(
        f"/api/workspaces/{workspace}/catalogs/{slug}/schemas/analytics/tables",
        json={"name": "bad", "columns": [{"name": "c", "type": "ARRAY"}]},
    )
    assert resp.status_code == 422, resp.text


async def test_metadata_catalog_is_not_reachable_from_user_sql(
    api_client, workspace, healthy_agent, slug
) -> None:
    """The escape the deny-list closes. Both forms are otherwise-allowed
    statement types, and both were verified reachable on DuckDB 1.5.5."""
    await _make_ducklake(api_client, workspace, slug)
    for sql in (
        f"SELECT count(*) FROM __ducklake_metadata_{slug}.cat_{slug}.ducklake_data_file",
        f"UPDATE __ducklake_metadata_{slug}.cat_{slug}.ducklake_data_file SET record_count = 0",
        "SELECT * FROM postgres_query('x', 'SELECT 1')",
    ):
        resp = await api_client.post(
            f"/api/workspaces/{workspace}/queries",
            json={"sql": sql, "agent_id": healthy_agent["id"]},
        )
        assert resp.status_code == 422, f"{sql!r} was not refused: {resp.text}"


async def test_iceberg_and_ducklake_join_in_one_query(
    api_client, workspace, catalog, healthy_agent, slug
) -> None:
    """The decisive test.

    `workspace` already has an Iceberg catalog attached (`catalog`). Attaching a
    DuckLake one beside it and joining across the two in a single statement is
    what proves catalog kind and storage backend are orthogonal axes rather than
    a claim. If this fails, the design is wrong, not the test.
    """
    await _make_ducklake(api_client, workspace, slug)
    agent_id = healthy_agent["id"]

    await _run(
        api_client,
        workspace,
        agent_id,
        f"CREATE TABLE {catalog}.analytics.ice AS SELECT i AS id FROM range(100) r(i)",
    )
    await _run(
        api_client,
        workspace,
        agent_id,
        f"CREATE TABLE {slug}.analytics.lake AS SELECT i AS id FROM range(50) r(i)",
    )

    joined = await _run(
        api_client,
        workspace,
        agent_id,
        f"SELECT count(*) AS c FROM {catalog}.analytics.ice i "
        f"JOIN {slug}.analytics.lake l ON l.id = i.id",
    )
    assert joined["status"] == "done", joined
    rows = (await api_client.get(f"/api/queries/{joined['id']}/rows")).json()
    assert rows["rows"] == [{"c": 50}]
