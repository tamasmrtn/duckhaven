"""Unit tests for schemas + tables endpoints (M3 Step 6)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from conftest import seed_workspace
from fake_polaris import FakePolaris
from httpx import AsyncClient
from sqlalchemy import select

from api.models.catalog import Catalog
from api.models.storage_backend import StorageBackend
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services import query as query_module
from api.services.auth import hash_password
from api.services.polaris import (
    PolarisBadRequestError,
    PolarisError,
    PolarisNotFoundError,
    PolarisSchema,
    PolarisServerError,
    PolarisSnapshot,
    PolarisTable,
)


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
        kind="object_store",
        name="primary",
        root_uri="",
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
    resp = await auth_client.post("/workspaces", json={"slug": slug, "name": slug.title()})
    assert resp.status_code == 201, resp.text
    # Workspaces no longer auto-create a catalog; attach a default one (its
    # polaris_name defaults to the catalog slug == the workspace slug) so the
    # legacy default-catalog schema routes these tests exercise resolve.
    cat = await auth_client.post(
        f"/workspaces/{slug}/catalogs",
        json={"name": slug.replace("-", "_"), "storage_backend_id": str(backend.id)},
    )
    assert cat.status_code == 201, cat.text
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
    assert "analytics" in names


async def test_list_schemas_self_heals_pre_m3_workspace(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, db_session
):
    """A catalog row whose Polaris catalog was never provisioned (e.g. a partial
    create) self-heals (create catalog + default schema) on first access."""
    slug = "premig"
    user_id = (
        (await db_session.execute(select(User).where(User.email == "owner@test.local")))
        .scalar_one()
        .id
    )
    await seed_workspace(db_session, user_id=user_id, slug=slug, name="Pre", role="reader")

    assert slug not in fake_polaris.catalogs  # not provisioned in Polaris yet
    resp = await auth_client.get(f"/workspaces/{slug}/schemas")
    assert resp.status_code == 200
    assert slug in fake_polaris.catalogs  # self-healed (polaris_name == slug)
    assert (slug, "analytics") in fake_polaris.schemas


async def test_create_schema_requires_writer(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    """Reader role on the workspace must not be able to create schemas."""
    slug = "readonly"
    ws = Workspace(slug=slug, name="RO")
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    from sqlalchemy import select

    user = (
        await db_session.execute(select(User).where(User.email == "owner@test.local"))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="reader"))
    await db_session.commit()

    resp = await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "staging"})
    assert resp.status_code == 403


async def test_create_schema_happy(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "staging"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "staging"
    assert body["catalog_name"] == slug
    assert isinstance(body["workspace_id"], str)
    assert (slug, "staging") in fake_polaris.schemas


async def test_create_schema_duplicate_is_409(auth_client: AsyncClient, backend: StorageBackend):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "analytics"})
    resp = await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "analytics"})
    assert resp.status_code == 409


async def test_non_member_cannot_list(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    """A user that isn't a workspace_member must be denied (403)."""
    ws = Workspace(slug="other", name="Other")
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
    # Every Iceberg REST table is catalog-managed by definition.
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


# --- snapshot history ---


async def _make_table(auth_client: AsyncClient, slug: str, name: str = "events") -> None:
    resp = await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": name, "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    assert resp.status_code == 201, resp.text


async def test_list_snapshots_returns_history(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await _make_table(auth_client, slug)
    # Seed newest-first history (the service guarantees ordering; the fake
    # returns what it is given).
    fake_polaris.snapshots[(slug, "main", "events")] = [
        PolarisSnapshot(
            snapshot_id=9223372036854775807,  # > JS safe-integer range
            parent_snapshot_id=11,
            timestamp_ms=2000,
            operation="overwrite",
            summary={"operation": "overwrite", "added-records": "3", "total-records": "8"},
            is_current=True,
        ),
        PolarisSnapshot(
            snapshot_id=11,
            timestamp_ms=1000,
            operation="append",
            summary={"operation": "append", "added-records": "5"},
        ),
    ]
    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables/events/snapshots")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["snapshot_id"] for r in rows] == ["9223372036854775807", "11"]
    assert rows[0]["is_current"] is True
    assert rows[0]["parent_snapshot_id"] == "11"
    assert rows[0]["operation"] == "overwrite"
    assert rows[0]["added_records"] == 3
    assert rows[0]["total_records"] == 8
    # Unrecorded metrics stay null rather than zero.
    assert rows[1]["total_records"] is None


async def test_list_snapshots_empty_for_table_without_history(
    auth_client: AsyncClient, backend: StorageBackend
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await _make_table(auth_client, slug)
    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables/events/snapshots")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_snapshots_404_for_unknown_table(
    auth_client: AsyncClient, backend: StorageBackend
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables/ghost/snapshots")
    assert resp.status_code == 404


async def test_list_snapshots_non_member_denied(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    ws = Workspace(slug="other", name="Other")
    db_session.add(ws)
    await db_session.commit()
    resp = await auth_client.get("/workspaces/other/schemas/main/tables/events/snapshots")
    assert resp.status_code == 403


async def test_create_table_requires_writer(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    slug = "readonly2"
    ws = Workspace(slug=slug, name="RO2")
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


async def test_drop_table_removes_sidecar(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    """Dropping a table also clears its TableMetadata sidecar row."""
    from sqlalchemy import select

    from api.models.table_metadata import TableMetadata

    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    rows = (await db_session.execute(select(TableMetadata))).scalars().all()
    assert any(r.table_name == "events" for r in rows)

    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/main/tables/events")
    assert resp.status_code == 204
    rows = (await db_session.execute(select(TableMetadata))).scalars().all()
    assert not any(r.table_name == "events" for r in rows)


# --- drop schema ---


async def test_drop_schema_empty(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "staging"})
    assert (slug, "staging") in fake_polaris.schemas

    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/staging")
    assert resp.status_code == 204
    assert (slug, "staging") not in fake_polaris.schemas


async def test_drop_schema_non_empty_requires_cascade(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "staging"})
    await auth_client.post(
        f"/workspaces/{slug}/schemas/staging/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )

    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/staging")
    assert resp.status_code == 409
    assert "events" in resp.json()["detail"]
    assert (slug, "staging") in fake_polaris.schemas  # not dropped


async def test_drop_schema_cascade_drops_tables(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, db_session
):
    from sqlalchemy import select

    from api.models.table_metadata import TableMetadata

    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "staging"})
    await auth_client.post(
        f"/workspaces/{slug}/schemas/staging/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )

    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/staging?cascade=true")
    assert resp.status_code == 204
    assert (slug, "staging") not in fake_polaris.schemas
    assert (slug, "staging", "events") not in fake_polaris.tables
    rows = (await db_session.execute(select(TableMetadata))).scalars().all()
    assert not any(r.table_name == "events" for r in rows)


# --- PolarisError -> HTTP exception handler ---


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (PolarisNotFoundError("missing namespace"), 404),
        (PolarisBadRequestError("bad request"), 422),
        (PolarisServerError("upstream exploded"), 502),
        (PolarisError("generic polaris failure"), 502),
    ],
)
async def test_polaris_error_maps_to_http_response(
    auth_client: AsyncClient,
    backend: StorageBackend,
    fake_polaris: FakePolaris,
    exc: PolarisError,
    expected_status: int,
):
    """A PolarisError escaping a route is rendered by the exception handler with
    the mapped status and the exception message echoed in `detail`."""
    slug = await _make_workspace(auth_client, backend, "alpha")
    fake_polaris.raise_on_list_tables = exc

    resp = await auth_client.get(f"/workspaces/{slug}/schemas/main/tables")
    assert resp.status_code == expected_status
    assert resp.json()["detail"] == str(exc)


async def test_drop_schema_requires_writer(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    slug = "readonly4"
    ws = Workspace(slug=slug, name="RO4")
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    from sqlalchemy import select

    user = (
        await db_session.execute(select(User).where(User.email == "owner@test.local"))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="reader"))
    await db_session.commit()

    resp = await auth_client.delete(f"/workspaces/{slug}/schemas/analytics")
    assert resp.status_code == 403


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


async def test_table_detail_surfaces_iceberg_metadata(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, db_session
):
    """Iceberg metadata flows end to end: format version from Polaris, and the
    agent-probed snapshot/file/delete fields from the QUERY_DONE frame."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from api.models.query import Query
    from api.services import query as query_service
    from duckhaven_shared.protocol import Frame, FrameType

    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "BIGINT"}]},
    )
    # Polaris reports the Iceberg format version on load.
    fake_polaris.tables[(slug, "main", "events")].format_version = 2

    ws = (await db_session.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one()
    query = Query(
        workspace_id=ws.id,
        sql="SELECT * FROM main.events",
        status="running",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={
                "query_id": str(query.id),
                "status": "done",
                "stats_table": {"catalog": slug, "schema": "main", "table": "events"},
                "table_row_count": 5,
                "iceberg": {
                    "snapshot_id": 7264354987654321234,
                    "snapshot_at": "2026-05-15T14:03:00+00:00",
                    "data_file_count": 128,
                    "has_deletes": True,
                },
            },
        ),
    )

    body = (await auth_client.get(f"/workspaces/{slug}/schemas/main/tables/events")).json()
    assert body["format_version"] == 2
    # 64-bit snapshot ids are serialized as strings to dodge JS precision loss.
    assert body["snapshot_id"] == "7264354987654321234"
    assert body["data_file_count"] == 128
    assert body["has_deletes"] is True
    assert body["snapshot_at"] is not None


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
    ws = Workspace(slug=slug, name="RO3")
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


# --- refresh stats (catalog Refresh button) ---


def _seed_worksheet_table(fake_polaris: FakePolaris, slug: str, schema: str, table: str) -> None:
    """A table that exists in Polaris but has no metadata sidecar — i.e. created
    from the worksheet, not the create-table endpoint, so its row count is unknown."""
    fake_polaris.schemas[(slug, schema)] = PolarisSchema(name=schema, catalog_name=slug)
    fake_polaris.tables[(slug, schema, table)] = PolarisTable(
        name=table, catalog_name=slug, schema_name=schema
    )


def _patch_probe(monkeypatch) -> list[tuple[str, str]]:
    """Stand in for the agent stats probe: record each (schema, table) probed and
    upsert the count the websocket handler would have written, returning 'done'."""
    calls: list[tuple[str, str]] = []

    async def fake_pick_agent_for(db, workspace):
        return SimpleNamespace(id=uuid4())

    async def fake_run_sync_query(db, *, workspace, user_id, stats_for, **kwargs):
        calls.append((stats_for["schema"], stats_for["table"]))
        # Upsert like the real websocket handler (a probed table may already have
        # a sidecar row, e.g. one created via the create-table endpoint). Metadata
        # is catalog-scoped, so resolve the catalog from the probe's slug.
        catalog = (
            await db.execute(select(Catalog).where(Catalog.slug == stats_for["catalog"]))
        ).scalar_one()
        existing = (
            await db.execute(
                select(TableMetadata).where(
                    TableMetadata.catalog_id == catalog.id,
                    TableMetadata.schema_name == stats_for["schema"],
                    TableMetadata.table_name == stats_for["table"],
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = TableMetadata(
                catalog_id=catalog.id,
                schema_name=stats_for["schema"],
                table_name=stats_for["table"],
            )
            db.add(existing)
        existing.row_count = 99
        await db.commit()
        return SimpleNamespace(status="done")

    monkeypatch.setattr(query_module, "pick_agent_for", fake_pick_agent_for)
    monkeypatch.setattr(query_module, "run_sync_query", fake_run_sync_query)
    return calls


async def test_refresh_stats_probes_only_tables_missing_a_count(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, monkeypatch
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    # A table created through the dialog already carries a (zero) count sidecar.
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "main"})
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "dialog_tbl", "columns": [{"name": "id", "type": "INTEGER"}]},
    )
    # Two worksheet-created tables have no sidecar at all.
    _seed_worksheet_table(fake_polaris, slug, "main", "ws_a")
    _seed_worksheet_table(fake_polaris, slug, "main", "ws_b")

    calls = _patch_probe(monkeypatch)
    resp = await auth_client.post(f"/workspaces/{slug}/schemas/refresh-stats")

    assert resp.status_code == 200, resp.text
    assert resp.json()["probed"] == 2
    # Only the two without a count are probed; the dialog table is left alone.
    assert sorted(calls) == [("main", "ws_a"), ("main", "ws_b")]


async def test_refresh_stats_noop_when_every_table_has_a_count(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, monkeypatch
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "main"})
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "dialog_tbl", "columns": [{"name": "id", "type": "INTEGER"}]},
    )

    calls = _patch_probe(monkeypatch)
    resp = await auth_client.post(f"/workspaces/{slug}/schemas/refresh-stats")

    assert resp.status_code == 200
    assert resp.json()["probed"] == 0
    assert calls == []  # nothing missing → no agent work issued


async def test_refresh_stats_503_when_no_agent_connected(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, monkeypatch
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    _seed_worksheet_table(fake_polaris, slug, "main", "ws_a")

    async def no_agent(db, workspace):
        return None

    monkeypatch.setattr(query_module, "pick_agent_for", no_agent)
    resp = await auth_client.post(f"/workspaces/{slug}/schemas/refresh-stats")
    assert resp.status_code == 503


async def test_refresh_stats_non_member_forbidden(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    ws = Workspace(slug="other", name="Other")
    db_session.add(ws)
    await db_session.commit()

    resp = await auth_client.post("/workspaces/other/schemas/refresh-stats")
    assert resp.status_code == 403


# --- recount a single table (right-click "Recount rows") ---


async def test_recount_reprobes_even_an_already_counted_table(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, monkeypatch
):
    """Recount re-measures regardless of the cached value — the freshness escape
    hatch the workspace-wide refresh deliberately skips."""
    slug = await _make_workspace(auth_client, backend, "alpha")
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "main"})
    await auth_client.post(
        f"/workspaces/{slug}/schemas/main/tables",
        json={"name": "events", "columns": [{"name": "id", "type": "INTEGER"}]},
    )

    calls = _patch_probe(monkeypatch)  # upserts row_count=99
    resp = await auth_client.post(f"/workspaces/{slug}/schemas/main/tables/events/recount")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"row_count": 99}
    assert calls == [("main", "events")]  # the already-counted table is re-probed


async def test_recount_404_for_unknown_table(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    resp = await auth_client.post(f"/workspaces/{slug}/schemas/main/tables/ghost/recount")
    assert resp.status_code == 404


async def test_recount_503_when_no_agent_connected(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris: FakePolaris, monkeypatch
):
    slug = await _make_workspace(auth_client, backend, "alpha")
    _seed_worksheet_table(fake_polaris, slug, "main", "events")

    async def no_agent(db, workspace):
        return None

    monkeypatch.setattr(query_module, "pick_agent_for", no_agent)
    resp = await auth_client.post(f"/workspaces/{slug}/schemas/main/tables/events/recount")
    assert resp.status_code == 503


async def test_recount_non_member_forbidden(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    ws = Workspace(slug="other", name="Other")
    db_session.add(ws)
    await db_session.commit()

    resp = await auth_client.post("/workspaces/other/schemas/main/tables/events/recount")
    assert resp.status_code == 403
