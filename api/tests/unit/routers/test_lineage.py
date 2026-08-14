"""The lineage import/purge API: authorization, validation, partial success."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from api.models.lineage import LineageEdge
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import hash_password

MANIFEST = (
    Path(__file__).resolve().parents[1] / "services" / "lineage" / "fixtures" / "manifest.json"
)


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="owner@l.local", password_hash=hash_password("pw"), name="Owner", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@l.local", "password": "pw"})
    return client


@pytest.fixture
async def ws(auth_client, owner, db_session):
    """A workspace attaching two catalogs, `warehouse` and `raw`."""
    resp = await auth_client.post("/workspaces", json={"slug": "lin", "name": "Lin"})
    slug = resp.json()["slug"]
    backend = StorageBackend(kind="object_store", name="p", root_uri="", created_by=owner.id)
    db_session.add(backend)
    await db_session.commit()
    await db_session.refresh(backend)
    for name in ("warehouse", "raw"):
        await auth_client.post(
            f"/workspaces/{slug}/catalogs",
            json={"name": name, "storage_backend_id": str(backend.id)},
        )
    return slug


def _edge(source_table="src", target_table="dim", source_catalog="raw"):
    return {
        "source": {"catalog": source_catalog, "schema": "analytics", "table": source_table},
        "target": {"catalog": "warehouse", "schema": "analytics", "table": target_table},
        "operation": "model",
    }


async def _stored(db_session) -> list[LineageEdge]:
    rows = await db_session.execute(sa.select(LineageEdge))
    return list(rows.scalars().all())


# --- generic import ---------------------------------------------------------


async def test_import_creates_edges(auth_client, ws, db_session):
    resp = await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "custom", "run_id": "r1", "edges": [_edge()]},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    assert [e.provider for e in await _stored(db_session)] == ["custom"]


async def test_import_is_idempotent(auth_client, ws, db_session):
    payload = {"provider": "custom", "run_id": "r1", "edges": [_edge()]}
    await auth_client.post(f"/workspaces/{ws}/lineage/imports", json=payload)
    resp = await auth_client.post(f"/workspaces/{ws}/lineage/imports", json=payload)

    assert resp.json() == {"created": 0, "updated": 1, "removed": 0, "skipped": []}
    assert len(await _stored(db_session)) == 1


async def test_schema_key_is_accepted_as_producers_spell_it(auth_client, ws):
    resp = await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={
            "provider": "custom",
            "edges": [
                {
                    "source": {"catalog": "raw", "schema_name": "analytics", "table": "a"},
                    "target": {"catalog": "warehouse", "schema": "analytics", "table": "b"},
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


async def test_execution_provider_is_reserved(auth_client, ws):
    resp = await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "execution", "edges": [_edge()]},
    )
    assert resp.status_code == 422
    assert "reserved" in resp.json()["detail"]


async def test_unresolvable_target_is_reported_not_fatal(auth_client, ws, db_session):
    resp = await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={
            "provider": "custom",
            "edges": [
                _edge(),
                {
                    "source": {"catalog": "raw", "schema": "analytics", "table": "x"},
                    "target": {"catalog": "nope", "schema": "analytics", "table": "y"},
                },
            ],
        },
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["created"] == 1  # the good edge still landed
    assert body["skipped"] == [{"ref": "nope.analytics.y", "reason": "unknown_catalog"}]


async def test_unknown_source_catalog_becomes_an_external_asset(auth_client, ws, db_session):
    await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "custom", "edges": [_edge(source_catalog="somewhere_else")]},
    )
    (edge,) = await _stored(db_session)
    assert edge.source_catalog_id is None
    assert edge.source_system == "somewhere_else"


async def test_reconcile_requires_a_run_id(auth_client, ws):
    resp = await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "custom", "reconcile": "provider_run", "edges": [_edge()]},
    )
    assert resp.status_code == 422


async def test_unknown_reconcile_mode_is_rejected(auth_client, ws):
    resp = await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "custom", "reconcile": "everything", "edges": [_edge()]},
    )
    assert resp.status_code == 422


async def test_oversize_payload_is_rejected(auth_client, ws):
    resp = await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "custom", "edges": [_edge(f"s{i}") for i in range(5001)]},
    )
    assert resp.status_code == 422


async def test_import_requires_writer(client, ws, db_session, owner):
    reader = User(email="r@l.local", password_hash=hash_password("pw"), name="R", role="user")
    db_session.add(reader)
    await db_session.commit()
    workspace = (
        await db_session.execute(sa.select(Workspace).where(Workspace.slug == ws))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=reader.id, role="reader"))
    await db_session.commit()

    await client.post("/auth/login", json={"email": "r@l.local", "password": "pw"})
    resp = await client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "custom", "edges": [_edge()]},
    )
    assert resp.status_code == 403


# --- dbt artifact import ----------------------------------------------------


async def test_dbt_manifest_import(auth_client, ws, db_session):
    manifest = json.loads(MANIFEST.read_text())
    resp = await auth_client.post(f"/workspaces/{ws}/lineage/imports/dbt", json=manifest)

    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] > 0
    edges = await _stored(db_session)
    assert {e.provider for e in edges} == {"dbt"}
    # The invocation id became the import batch.
    assert {e.provider_run_id for e in edges} == {"b1a7c0de-0000-4000-8000-000000000001"}
    assert ("stg_orders", "dim_orders_v2") in {(e.source_table, e.target_table) for e in edges}


async def test_dbt_reimport_reconciles_stale_edges(auth_client, ws, db_session):
    manifest = json.loads(MANIFEST.read_text())
    await auth_client.post(f"/workspaces/{ws}/lineage/imports/dbt", json=manifest)

    # The model now reads a different staging table, under a new invocation.
    manifest["metadata"]["invocation_id"] = "b1a7c0de-0000-4000-8000-000000000002"
    manifest["parent_map"]["model.acme.dim_orders"] = ["seed.acme.country_codes"]
    resp = await auth_client.post(f"/workspaces/{ws}/lineage/imports/dbt", json=manifest)

    assert resp.json()["removed"] >= 1
    pairs = {(e.source_table, e.target_table) for e in await _stored(db_session)}
    assert ("stg_orders", "dim_orders_v2") not in pairs
    assert ("country_codes", "dim_orders_v2") in pairs


async def test_dbt_import_never_removes_execution_lineage(auth_client, ws, db_session):
    manifest = json.loads(MANIFEST.read_text())
    await auth_client.post(f"/workspaces/{ws}/lineage/imports/dbt", json=manifest)
    # Something DuckHaven observed itself, into the same target dbt builds.
    await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "observed", "edges": [_edge("other", "dim_orders_v2")]},
    )

    manifest["metadata"]["invocation_id"] = "b1a7c0de-0000-4000-8000-000000000003"
    await auth_client.post(f"/workspaces/{ws}/lineage/imports/dbt", json=manifest)

    providers = {e.provider for e in await _stored(db_session)}
    assert "observed" in providers


async def test_unknown_provider_artifact_is_rejected(auth_client, ws):
    resp = await auth_client.post(f"/workspaces/{ws}/lineage/imports/nope", json={})
    assert resp.status_code == 422
    assert "No lineage adapter" in resp.json()["detail"]


# --- purge ------------------------------------------------------------------


async def test_purge_removes_only_the_named_provider(auth_client, ws, db_session):
    await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "a", "edges": [_edge("s1")]},
    )
    await auth_client.post(
        f"/workspaces/{ws}/lineage/imports",
        json={"provider": "b", "edges": [_edge("s2")]},
    )

    resp = await auth_client.request(
        "DELETE", f"/workspaces/{ws}/lineage/imports", params={"provider": "a"}
    )

    assert resp.status_code == 200
    assert resp.json()["removed"] == 1
    assert [e.provider for e in await _stored(db_session)] == ["b"]


async def test_purge_refuses_execution_lineage(auth_client, ws):
    resp = await auth_client.request(
        "DELETE", f"/workspaces/{ws}/lineage/imports", params={"provider": "execution"}
    )
    assert resp.status_code == 422
