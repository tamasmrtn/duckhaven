"""Unit tests for schemas + tables endpoints (M3 Step 6)."""

from __future__ import annotations

import pytest
from fake_polaris import FakePolaris
from httpx import AsyncClient

from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import hash_password


@pytest.fixture
async def owner(db_session) -> User:
    u = User(
        email="owner@test.local",
        password_hash=hash_password("pw"),
        name="Owner",
        role="user",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def backend(db_session, owner: User) -> StorageBackend:
    sb = StorageBackend(
        kind="local_fs",
        name="local",
        root_uri="file:///var/duckhaven/data",
        created_by=owner.id,
    )
    db_session.add(sb)
    await db_session.commit()
    await db_session.refresh(sb)
    return sb


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@test.local", "password": "pw"})
    return client


async def _make_workspace(
    auth_client: AsyncClient, backend: StorageBackend, slug: str = "alpha"
) -> str:
    resp = await auth_client.post(
        "/workspaces",
        json={"slug": slug, "name": slug.title(), "storage_backend_id": str(backend.id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["slug"]


# --- list/create schema ---


async def test_list_schemas_self_heals_catalog(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    # The eager workspace-create path already provisioned the catalog +
    # main schema. Sanity-check that list returns the default schema.
    resp = await auth_client.get(f"/workspaces/{slug}/schemas")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.json()}
    assert "main" in names


async def test_list_schemas_self_heals_pre_m3_workspace(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, db_session
):
    """A workspace row that pre-dates M3 has no UC catalog — listing
    schemas must self-heal (create catalog + main schema) on first access."""
    slug = "premig"
    ws = Workspace(slug=slug, name="Pre", storage_backend_id=backend.id)
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    # Make the authed user a member so RBAC passes.
    from sqlalchemy import select

    user_id = (
        (await db_session.execute(select(User).where(User.email == "owner@test.local")))
        .scalar_one()
        .id
    )
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user_id, role="reader"))
    await db_session.commit()

    assert slug not in fake_polaris.catalogs  # no catalog yet
    resp = await auth_client.get(f"/workspaces/{slug}/schemas")
    assert resp.status_code == 200
    assert slug in fake_polaris.catalogs  # self-healed
    assert (slug, "main") in fake_polaris.schemas


async def test_create_schema_requires_writer(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    """Reader role on the workspace must not be able to create schemas."""
    slug = "readonly"
    ws = Workspace(slug=slug, name="RO", storage_backend_id=backend.id)
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    from sqlalchemy import select

    user = (
        await db_session.execute(select(User).where(User.email == "owner@test.local"))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="reader"))
    await db_session.commit()

    resp = await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "analytics"})
    assert resp.status_code == 403


async def test_create_schema_happy(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "analytics"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "analytics"
    assert body["catalog_name"] == slug
    assert isinstance(body["workspace_id"], str)
    assert (slug, "analytics") in fake_polaris.schemas


async def test_create_schema_duplicate_is_409(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "analytics"})
    resp = await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "analytics"})
    assert resp.status_code == 409


async def test_non_member_cannot_list(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    """A user that isn't a workspace_member must be denied (403)."""
    ws = Workspace(slug="other", name="Other", storage_backend_id=backend.id)
    db_session.add(ws)
    await db_session.commit()

    resp = await auth_client.get("/workspaces/other/schemas")
    assert resp.status_code == 403


# --- create table ---


async def test_create_table_is_iceberg_with_mapped_columns(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={
            "name": "events",
            "columns": [
                {"name": "ts", "type": "TIMESTAMP", "nullable": False},
                {"name": "user_id", "type": "VARCHAR"},
                {"name": "amount", "type": "DOUBLE"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "events"
    # Iceberg tables are catalog-managed; no Delta feature properties.
    assert body["format"] == body["data_source_format"] == "ICEBERG"
    assert body["catalog_commits"] is True
    # Columns map to Iceberg primitive types (display type_name upper-cased).
    types = [(c["name"], c["type_name"]) for c in body["columns"]]
    assert types == [("ts", "TIMESTAMP"), ("user_id", "STRING"), ("amount", "DOUBLE")]
    # The body sent to Polaris carries Iceberg schema fields (id/required/type).
    sent = fake_polaris.created_table_bodies[-1]["columns"]
    assert sent[0] == {"id": 1, "name": "ts", "required": True, "type": "timestamp"}
    assert sent[1]["type"] == "string" and sent[1]["required"] is False


async def test_create_table_rejects_unknown_type(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={
            "name": "weird",
            "columns": [{"name": "blob", "type": "BLOB"}],
        },
    )
    assert resp.status_code == 422  # AllowedColumnType literal mismatch


async def test_list_tables_returns_created_one(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert names == ["events"]


async def test_get_table_404(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables/ghost")
    assert resp.status_code == 404


async def test_create_table_requires_writer(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    slug = "readonly2"
    ws = Workspace(slug=slug, name="RO2", storage_backend_id=backend.id)
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    from sqlalchemy import select

    user = (
        await db_session.execute(select(User).where(User.email == "owner@test.local"))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="reader"))
    await db_session.commit()

    resp = await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    assert resp.status_code == 403


# --- drop table ---


async def test_drop_table_writer(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    assert (slug, "main", "events") in fake_polaris.tables

    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/main/tables/events")
    assert resp.status_code == 204
    assert (slug, "main", "events") not in fake_polaris.tables


async def test_drop_table_missing_is_404(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/main/tables/ghost")
    assert resp.status_code == 404


# --- catalog enrichment ---


async def test_create_table_enriches_metadata(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert isinstance(body["workspace_id"], str)
    assert body["format"] == body["data_source_format"] == "ICEBERG"
    assert body["catalog_commits"] is True  # every Iceberg REST table is catalog-managed
    assert body["columns"][0]["type"]  # simple display type present
    # Creator becomes owner + last writer; stats start empty.
    assert body["owner"] == "Owner"
    assert body["last_write_by"] == "Owner"
    assert body["row_count"] == 0


async def test_list_schemas_includes_workspace_id(
    auth_client: AsyncClient, backend: StorageBackend
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "analytics"})
    resp = await auth_client.get(f"/workspaces/{slug}/schemas")
    assert resp.status_code == 200
    assert all(isinstance(s["workspace_id"], str) for s in resp.json())


# --- sample preview ---


async def test_sample_503_when_no_agent(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables/events/sample")
    assert resp.status_code == 503


async def test_sample_returns_rows(auth_client: AsyncClient, backend: StorageBackend, monkeypatch):
    import os
    import tempfile
    import types
    import uuid as uuidlib

    import duckdb
    import httpx

    from api.models.query import Query
    from api.services import query as query_service

    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )

    def _parquet_bytes() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            path = fh.name
        try:
            duckdb.connect().execute(f"COPY (SELECT 7 AS id) TO '{path}' (FORMAT PARQUET)")
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    fake_agent = types.SimpleNamespace(id=uuidlib.uuid4())

    async def fake_pick(db, workspace):
        return fake_agent

    async def fake_run(db, **kwargs):
        return Query(
            workspace_id=uuidlib.uuid4(),
            sql=kwargs["sql"],
            status="done",
            row_count=1,
            result_path="/results/x.parquet",
        )

    async def fake_token(db, agent_id):
        return "tok"

    async def fake_proxy(agent, query, range_header=None, *, token=None):
        return httpx.Response(200, content=_parquet_bytes())

    monkeypatch.setattr(query_service, "pick_agent_for", fake_pick)
    monkeypatch.setattr(query_service, "run_sync_query", fake_run)
    monkeypatch.setattr(query_service, "agent_session_token", fake_token)
    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables/events/sample")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == ["id"]
    assert body["rows"] == [{"id": 7}]


async def test_drop_table_requires_writer(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    slug = "readonly3"
    ws = Workspace(slug=slug, name="RO3", storage_backend_id=backend.id)
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    from sqlalchemy import select

    user = (
        await db_session.execute(select(User).where(User.email == "owner@test.local"))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="reader"))
    await db_session.commit()

    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/main/tables/events")
    assert resp.status_code == 403
