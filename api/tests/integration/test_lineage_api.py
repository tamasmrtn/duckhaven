"""Lineage persistence and reads against real Postgres + real Polaris.

Covers what the SQLite unit tests cannot: that the schema, indexes and unique
constraint behave on the real backend, and that the workspace boundary holds
when the catalogs are genuine Polaris catalogs rather than fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from api.models.catalog import Catalog
from api.models.lineage import LineageEdge
from api.models.table_metadata import TableMetadata
from api.services.lineage.identity import reconcile_table_identity

pytestmark = pytest.mark.integration

MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "unit"
    / "services"
    / "lineage"
    / "fixtures"
    / "manifest.json"
)


async def _workspace_with_catalog(admin_client, workspace_factory) -> tuple[str, str]:
    slug = f"dh-lin-{uuid4().hex[:8]}"
    await workspace_factory(slug=slug, name="Lineage")
    catalog = f"c_{slug.replace('-', '_')}"
    created = await admin_client.post(f"/workspaces/{slug}/catalogs", json={"name": catalog})
    assert created.status_code == 201, created.text
    return slug, catalog


def _edge(catalog: str, source: str, target: str) -> dict:
    return {
        "source": {"catalog": catalog, "schema": "analytics", "table": source},
        "target": {"catalog": catalog, "schema": "analytics", "table": target},
        "operation": "model",
    }


async def _graph(admin_client, ws: str, catalog: str, table: str, **params) -> dict:
    resp = await admin_client.get(
        f"/workspaces/{ws}/catalogs/{catalog}/schemas/analytics/tables/{table}/lineage",
        params=params,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_imported_lineage_round_trips(admin_client, workspace_factory) -> None:
    ws, catalog = await _workspace_with_catalog(admin_client, workspace_factory)

    imported = await admin_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "dbt", "run_id": "r1", "edges": [_edge(catalog, "src", "dim")]},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] == 1

    graph = await _graph(admin_client, ws, catalog, "dim", direction="upstream")
    assert {n["table"] for n in graph["nodes"]} == {"src", "dim"}
    assert [p["name"] for p in graph["edges"][0]["providers"]] == ["dbt"]


async def test_the_unique_constraint_makes_reimport_idempotent(
    admin_client, workspace_factory
) -> None:
    ws, catalog = await _workspace_with_catalog(admin_client, workspace_factory)
    payload = {"provider": "dbt", "run_id": "r1", "edges": [_edge(catalog, "src", "dim")]}

    await admin_client.post(f"/workspaces/{ws}/lineage/imports", json=payload)
    second = await admin_client.post(f"/workspaces/{ws}/lineage/imports", json=payload)

    assert second.json() == {"created": 0, "updated": 1, "removed": 0, "skipped": []}
    graph = await _graph(admin_client, ws, catalog, "dim", direction="upstream")
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["observation_count"] == 2


async def test_two_providers_coexist_on_one_pair(admin_client, workspace_factory) -> None:
    ws, catalog = await _workspace_with_catalog(admin_client, workspace_factory)
    for provider in ("dbt", "other_tool"):
        resp = await admin_client.post(
            f"/workspaces/{ws}/lineage/imports",
            json={"provider": provider, "edges": [_edge(catalog, "src", "dim")]},
        )
        assert resp.status_code == 200, resp.text

    graph = await _graph(admin_client, ws, catalog, "dim", direction="upstream")
    assert [p["name"] for p in graph["edges"][0]["providers"]] == ["dbt", "other_tool"]


async def test_a_catalog_in_another_workspace_is_invisible(admin_client, workspace_factory) -> None:
    """The workspace read boundary, with two real catalogs."""
    ws_a, catalog_a = await _workspace_with_catalog(admin_client, workspace_factory)
    ws_b, catalog_b = await _workspace_with_catalog(admin_client, workspace_factory)

    # An edge whose source lives in the *other* workspace's catalog.
    resp = await admin_client.post(
        f"/workspaces/{ws_a}/lineage/imports",
        json={
            "provider": "dbt",
            "edges": [
                {
                    "source": {"catalog": catalog_b, "schema": "analytics", "table": "far"},
                    "target": {"catalog": catalog_a, "schema": "analytics", "table": "dim"},
                }
            ],
        },
    )
    # `catalog_b` is unknown to workspace A, so its source becomes external
    # rather than silently reaching across the boundary.
    assert resp.status_code == 200, resp.text

    graph = await _graph(admin_client, ws_a, catalog_a, "dim", direction="upstream")
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "external" in kinds
    assert all(n["catalog"] != catalog_b for n in graph["nodes"])

    # And workspace B knows nothing about it.
    other = await _graph(admin_client, ws_b, catalog_b, "far")
    assert other["edges"] == []


async def test_dbt_manifest_import_end_to_end(admin_client, workspace_factory) -> None:
    slug = f"dh-lin-{uuid4().hex[:8]}"
    await workspace_factory(slug=slug, name="Lineage")
    # The fixture manifest targets `warehouse` and sources from `raw`.
    for name in ("warehouse", "raw"):
        created = await admin_client.post(f"/workspaces/{slug}/catalogs", json={"name": name})
        assert created.status_code in (201, 409), created.text

    manifest = json.loads(MANIFEST.read_text())
    resp = await admin_client.post(f"/workspaces/{slug}/lineage/imports/dbt", json=manifest)
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] > 0

    graph = await _graph(admin_client, slug, "warehouse", "dim_orders_v2", direction="upstream")
    assert "stg_orders" in {n["table"] for n in graph["nodes"]}


async def test_traversal_depth_is_honoured_on_postgres(admin_client, workspace_factory) -> None:
    ws, catalog = await _workspace_with_catalog(admin_client, workspace_factory)
    chain = ["a", "b", "c", "d"]
    await admin_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={
            "provider": "dbt",
            "edges": [
                _edge(catalog, source, target)
                for source, target in zip(chain, chain[1:], strict=False)
            ],
        },
    )

    shallow = await _graph(admin_client, ws, catalog, "d", direction="upstream", depth=1)
    deep = await _graph(admin_client, ws, catalog, "d", direction="upstream", depth=3)

    assert {n["table"] for n in shallow["nodes"]} == {"c", "d"}
    assert {n["table"] for n in deep["nodes"]} == {"a", "b", "c", "d"}


async def test_a_rename_carries_lineage_under_the_real_unique_constraint(
    admin_client, workspace_factory, db_session
) -> None:
    """Rekeying can collide with an edge already at the new address. On SQLite
    the merge is easy to get accidentally right; this proves it under the real
    constraint, where getting it wrong aborts the transaction."""
    ws, catalog = await _workspace_with_catalog(admin_client, workspace_factory)
    for source, target in (("src", "dim"), ("src", "dim_v2")):
        resp = await admin_client.post(
            f"/workspaces/{ws}/lineage/imports",
            json={"provider": "dbt", "edges": [_edge(catalog, source, target)]},
        )
        assert resp.status_code == 200, resp.text

    cat = (await db_session.execute(sa.select(Catalog).where(Catalog.slug == catalog))).scalar_one()
    table_uuid = str(uuid4())
    db_session.add(
        TableMetadata(
            catalog_id=cat.id,
            schema_name="analytics",
            table_name="dim",
            table_uuid=table_uuid,
        )
    )
    await db_session.commit()

    outcome = await reconcile_table_identity(
        db_session,
        catalog_id=cat.id,
        schema="analytics",
        table="dim_v2",
        table_uuid=table_uuid,
    )
    await db_session.commit()
    assert outcome == "renamed"

    rows = list(
        (
            await db_session.execute(
                sa.select(LineageEdge).where(LineageEdge.target_catalog_id == cat.id)
            )
        )
        .scalars()
        .all()
    )
    assert [r.target_table for r in rows] == ["dim_v2"]
    assert rows[0].observation_count == 2
