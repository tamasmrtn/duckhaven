"""Lineage across the whole chain: real API + real agent + real catalog.

The one test that proves the parts fit together. Everything else about lineage
is unit-tested against fakes; this exercises the seam those tests cannot reach —
that a `QUERY_DONE` frame arriving over the live control channel actually causes
an edge to be written, and that the read endpoint then returns it.
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


async def _lineage(api_client, workspace: str, catalog: str, table: str, **params) -> dict:
    resp = await api_client.get(
        f"/api/workspaces/{workspace}/catalogs/{catalog}/schemas/analytics/tables/{table}/lineage",
        params=params,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def catalog(workspace: str) -> str:
    return f"c_{workspace.replace('-', '_')}"


async def test_ctas_records_lineage(api_client, workspace, healthy_agent, catalog) -> None:
    agent = healthy_agent["id"]
    await _run(api_client, workspace, agent, "CREATE TABLE lin_src AS SELECT 1 AS n")
    built = await _run(
        api_client, workspace, agent, "CREATE TABLE lin_dim AS SELECT n FROM lin_src"
    )
    assert built["status"] == "done", built

    graph = await _lineage(api_client, workspace, catalog, "lin_dim", direction="upstream")

    tables = {n["table"] for n in graph["nodes"]}
    assert tables == {"lin_src", "lin_dim"}
    (edge,) = graph["edges"]
    assert edge["providers"] == ["execution"]
    assert edge["operation"] == "create_table_as"
    # The click-through to the SQL that produced the relationship.
    assert edge["last_query_id"] == built["id"]
    # Column-level lineage is not derived yet, and says so rather than being absent.
    assert edge["columns"] == []


async def test_downstream_is_visible_from_the_source(
    api_client, workspace, healthy_agent, catalog
) -> None:
    agent = healthy_agent["id"]
    await _run(api_client, workspace, agent, "CREATE TABLE dn_src AS SELECT 1 AS n")
    await _run(api_client, workspace, agent, "CREATE TABLE dn_dim AS SELECT n FROM dn_src")

    graph = await _lineage(api_client, workspace, catalog, "dn_src", direction="downstream")

    assert {n["table"] for n in graph["nodes"]} == {"dn_src", "dn_dim"}


async def test_a_read_records_no_lineage(api_client, workspace, healthy_agent, catalog) -> None:
    agent = healthy_agent["id"]
    await _run(api_client, workspace, agent, "CREATE TABLE ro_src AS SELECT 1 AS n")
    await _run(api_client, workspace, agent, "SELECT * FROM ro_src")

    graph = await _lineage(api_client, workspace, catalog, "ro_src")

    assert graph["edges"] == []


async def test_creating_a_view_is_still_unsupported_by_the_engine(
    api_client, workspace, healthy_agent, catalog
) -> None:
    """Pins the reason there is no view-lineage test here.

    The extractor handles ``CREATE VIEW`` and is unit-tested for it, but DuckDB's
    Iceberg extension does not implement view creation ("Not implemented Error:
    Create View"), so no view can exist in a DuckHaven catalog to have lineage.
    When that lands upstream this test starts failing, which is the signal to
    replace it with the real end-to-end case.
    """
    agent = healthy_agent["id"]
    await _run(api_client, workspace, agent, "CREATE TABLE v_base AS SELECT 1 AS n")
    created = await _run(api_client, workspace, agent, "CREATE VIEW v_mid AS SELECT n FROM v_base")

    assert created["status"] == "failed"
    assert "Create View" in (created["error"] or "")


async def test_imported_lineage_merges_with_execution_lineage(
    api_client, workspace, healthy_agent, catalog
) -> None:
    agent = healthy_agent["id"]
    await _run(api_client, workspace, agent, "CREATE TABLE mg_src AS SELECT 1 AS n")
    await _run(api_client, workspace, agent, "CREATE TABLE mg_dim AS SELECT n FROM mg_src")

    imported = await api_client.post(
        f"/api/workspaces/{workspace}/lineage/imports",
        json={
            "provider": "dbt",
            "run_id": "xc-run-1",
            "edges": [
                {
                    "source": {
                        "catalog": catalog,
                        "schema": "analytics",
                        "table": "mg_src",
                    },
                    "target": {
                        "catalog": catalog,
                        "schema": "analytics",
                        "table": "mg_dim",
                    },
                    "operation": "model",
                }
            ],
        },
    )
    assert imported.status_code == 200, imported.text

    graph = await _lineage(api_client, workspace, catalog, "mg_dim", direction="upstream")

    # Both producers describe the same relationship: one edge, two providers.
    (edge,) = graph["edges"]
    assert edge["providers"] == ["dbt", "execution"]


async def test_dropping_a_table_removes_its_lineage(
    api_client, workspace, healthy_agent, catalog
) -> None:
    agent = healthy_agent["id"]
    await _run(api_client, workspace, agent, "CREATE TABLE dr_src AS SELECT 1 AS n")
    await _run(api_client, workspace, agent, "CREATE TABLE dr_dim AS SELECT n FROM dr_src")
    assert (await _lineage(api_client, workspace, catalog, "dr_dim"))["edges"]

    dropped = await api_client.delete(
        f"/api/workspaces/{workspace}/catalogs/{catalog}/schemas/analytics/tables/dr_src"
    )
    assert dropped.status_code == 204, dropped.text

    graph = await _lineage(api_client, workspace, catalog, "dr_dim")
    assert graph["edges"] == []
