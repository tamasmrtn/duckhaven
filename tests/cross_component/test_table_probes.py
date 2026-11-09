"""Table preview, recount and the health scan, through the real agent.

Each of these asks the agent to probe one table and report on it with the query's
completion; the API records the report only for the table it dispatched. The unit
suites build that report by hand, so only here does the agent's own echo of the
table meet the API's check, and only here does the preview's generated SQL pass
the query gate and run on a real catalog.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import quote

import pytest
import pytest_asyncio

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


async def _poll(predicate, timeout: float, interval: float = 0.5):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


def _table_url(workspace: str, catalog: str, table: str) -> str:
    return (
        f"/api/workspaces/{workspace}/catalogs/{catalog}/schemas/analytics/tables/"
        f"{quote(table, safe='')}"
    )


def _sql_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _create(api_client, workspace, agent_id, catalog: str, table: str, rows: int) -> None:
    created = await _run(
        api_client,
        workspace,
        agent_id,
        f"CREATE TABLE {catalog}.analytics.{_sql_name(table)} AS "
        f"SELECT i::INTEGER AS id FROM range({rows}) r(i)",
    )
    assert created["status"] == "done", created


async def _recorded_row_count(api_client, workspace, catalog, table, expected: int) -> bool:
    """Poll the table until the recount's report is recorded: the recount answers
    once its run is done, and the report is written just after."""

    async def _matches() -> bool:
        resp = await api_client.get(_table_url(workspace, catalog, table))
        return resp.status_code == 200 and resp.json().get("row_count") == expected

    return bool(await _poll(_matches, timeout=30.0))


async def _assert_previewed_and_recounted(api_client, workspace, agent_id, catalog, table) -> None:
    await _create(api_client, workspace, agent_id, catalog, table, rows=3)

    preview = await api_client.get(_table_url(workspace, catalog, table) + "/sample")
    assert preview.status_code == 200, preview.text
    assert sorted(int(row["id"]) for row in preview.json()["rows"]) == [0, 1, 2]

    recount = await api_client.post(_table_url(workspace, catalog, table) + "/recount")
    assert recount.status_code == 200, recount.text
    assert await _recorded_row_count(api_client, workspace, catalog, table, 3)

    # A later probe of the same table replaces the count, rather than the first
    # report being the only one that lands.
    inserted = await _run(
        api_client,
        workspace,
        agent_id,
        f"INSERT INTO {catalog}.analytics.{_sql_name(table)} SELECT 3 UNION ALL SELECT 4",
    )
    assert inserted["status"] == "done", inserted
    recount = await api_client.post(_table_url(workspace, catalog, table) + "/recount")
    assert recount.status_code == 200, recount.text
    assert await _recorded_row_count(api_client, workspace, catalog, table, 5)


async def test_an_iceberg_table_is_previewed_and_recounted(
    api_client, workspace, catalog, healthy_agent
) -> None:
    await _assert_previewed_and_recounted(
        api_client, workspace, healthy_agent["id"], catalog, "probed"
    )


@pytest_asyncio.fixture
async def ducklake_catalog(api_client, workspace):
    """A DuckLake catalog in ``workspace``, dropped with its data on teardown."""
    if not os.getenv("DUCKLAKE_DATABASE_URL"):
        pytest.skip("DUCKLAKE_DATABASE_URL not set")
    name = f"dl_{uuid.uuid4().hex[:8]}"
    created = await api_client.post(
        f"/api/workspaces/{workspace}/catalogs", json={"name": name, "kind": "ducklake"}
    )
    assert created.status_code == 201, created.text
    yield name

    await api_client.delete(f"/api/workspaces/{workspace}/catalogs/{name}")
    await api_client.delete(f"/api/catalogs/{created.json()['id']}")


async def test_a_ducklake_table_named_with_a_quote_is_previewed_and_recounted(
    api_client, workspace, ducklake_catalog, healthy_agent
) -> None:
    """The preview and the agent's row-count probe both quote the name they build."""
    await _assert_previewed_and_recounted(
        api_client, workspace, healthy_agent["id"], ducklake_catalog, 'probed "q" table'
    )


async def test_a_health_scan_records_a_sample_for_the_table(
    api_client, workspace, catalog, healthy_agent
) -> None:
    await _create(api_client, workspace, healthy_agent["id"], catalog, "scanned", rows=10)

    async def _sampled() -> dict | None:
        # Each forced cycle probes the next batch of tables in the deployment, so
        # re-trigger until this one's turn comes round.
        scan = await api_client.post("/api/admin/maintenance/scan")
        assert scan.status_code == 200, scan.text
        for _ in range(10):
            health = await api_client.get(f"/api/workspaces/{workspace}/health")
            assert health.status_code == 200, health.text
            sample = next(
                (
                    t
                    for t in health.json()["tables"]
                    if (t["schema_name"], t["table_name"]) == ("analytics", "scanned")
                ),
                None,
            )
            if sample is not None:
                return sample
            await asyncio.sleep(0.5)
        return None

    sample = await _poll(_sampled, timeout=90.0, interval=0.0)
    assert sample is not None, "the health scan recorded no sample for the table"
    assert sample["scanned_at"] is not None
    assert sample["data_file_count"] >= 1
