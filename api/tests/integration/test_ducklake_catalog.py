"""DuckLake provisioning and metadata reads against a live Postgres.

Covers what the unit suite cannot: real schemas and grants, metadata reads
matching the DuckLake 1.0 table shapes, and a never-attached catalog reading
empty. Env-gated on ``DUCKLAKE_DATABASE_URL``; the DuckDB half additionally
needs ``DUCKLAKE_AGENT_PASSWORD`` and ``DUCKLAKE_TEST_S3_ENDPOINT``.
"""

from __future__ import annotations

import os
import uuid

import pytest

from api.config import settings
from api.models.catalog import KIND_DUCKLAKE, Catalog
from api.services.catalog_backends import CatalogBackendNotFound, backend_for
from api.services.catalog_backends.ducklake import (
    agent_role_for,
    dispose_engine,
    metadata_schema_for,
    new_role_password,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _ducklake_settings():
    url = os.getenv("DUCKLAKE_DATABASE_URL")
    if not url:
        pytest.skip("DUCKLAKE_DATABASE_URL not set; skipping DuckLake catalog test")
    original = (settings.ducklake_enabled, settings.ducklake_database_url)
    settings.ducklake_enabled = True
    settings.ducklake_database_url = url
    yield
    settings.ducklake_enabled, settings.ducklake_database_url = original


@pytest.fixture
async def catalog():
    """A uniquely-named catalog, provisioned and torn down."""
    slug = f"it_{uuid.uuid4().hex[:8]}"
    cat = Catalog(
        slug=slug, name=slug, kind=KIND_DUCKLAKE, metadata_schema=metadata_schema_for(slug)
    )
    # Mirrors `create_catalog`: the password is minted before provisioning.
    cat.pending_ducklake_password = new_role_password()
    backend = backend_for(cat)
    await backend.provision(cat)
    try:
        yield cat
    finally:
        await backend.deprovision(cat)
        await dispose_engine()


@pytest.mark.asyncio
async def test_provision_creates_the_metadata_schema(catalog):
    from sqlalchemy import text

    from api.services.catalog_backends.ducklake import get_engine

    async with get_engine().connect() as conn:
        found = await conn.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :n"),
            {"n": catalog.metadata_schema},
        )
        assert found.scalar_one_or_none() == 1


@pytest.mark.asyncio
async def test_provision_is_idempotent(catalog):
    """Called on browse as a self-heal."""
    backend = backend_for(catalog)
    await backend.provision(catalog)
    await backend.provision(catalog)


@pytest.mark.asyncio
async def test_the_agent_role_is_granted_on_the_new_schema(catalog):
    """The extension builds its tables on first attach and needs this."""
    from sqlalchemy import text

    from api.services.catalog_backends.ducklake import get_engine

    async with get_engine().connect() as conn:
        result = await conn.execute(
            text(
                "SELECT has_schema_privilege(:role, :schema, 'CREATE'), "
                "has_schema_privilege(:role, :schema, 'USAGE')"
            ),
            {"role": agent_role_for(catalog.slug), "schema": catalog.metadata_schema},
        )
        assert result.one() == (True, True)


@pytest.mark.asyncio
async def test_a_never_attached_catalog_reads_as_empty(catalog):
    """The ducklake_* tables do not exist until an agent attaches, so a fresh
    catalog browses as empty rather than failing."""
    backend = backend_for(catalog)
    assert await backend.list_schemas(catalog) == []
    assert await backend.list_tables(catalog, "analytics") == []
    assert await backend.list_snapshots(catalog, "analytics", "events") == []
    with pytest.raises(CatalogBackendNotFound):
        await backend.get_table(catalog, "analytics", "events")


@pytest.mark.asyncio
async def test_deprovision_removes_the_schema(catalog):
    from sqlalchemy import text

    from api.services.catalog_backends.ducklake import get_engine

    backend = backend_for(catalog)
    await backend.deprovision(catalog)
    async with get_engine().connect() as conn:
        found = await conn.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :n"),
            {"n": catalog.metadata_schema},
        )
        assert found.scalar_one_or_none() is None
    # Re-provision for the fixture's teardown.
    await backend.provision(catalog)


# --- Through the HTTP API ---------------------------------------------------


@pytest.mark.asyncio
async def test_create_ducklake_catalog_through_the_api(admin_client, workspace_factory):
    """An operator can create one, and it describes itself honestly."""
    ws = await workspace_factory()
    slug = f"api_{uuid.uuid4().hex[:8]}"
    resp = await admin_client.post(
        f"/workspaces/{ws['slug']}/catalogs", json={"name": slug, "kind": "ducklake"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "ducklake"
    assert body["metadata_schema"] == f"cat_{slug}"
    # Exactly one identity per ck_catalogs_kind_identity.
    assert body["polaris_name"] is None
    caps = body["capabilities"]
    assert caps["external_engine_readable"] is False

    # Droppable, purging its prefix and schema.
    detach = await admin_client.delete(f"/workspaces/{ws['slug']}/catalogs/{slug}")
    assert detach.status_code == 204, detach.text
    drop = await admin_client.delete(f"/catalogs/{body['id']}")
    assert drop.status_code == 204, drop.text


@pytest.mark.asyncio
async def test_creating_a_ducklake_catalog_is_refused_when_disabled(
    admin_client, workspace_factory
):
    ws = await workspace_factory()
    original = settings.ducklake_enabled
    settings.ducklake_enabled = False
    try:
        resp = await admin_client.post(
            f"/workspaces/{ws['slug']}/catalogs",
            json={"name": f"off_{uuid.uuid4().hex[:6]}", "kind": "ducklake"},
        )
        assert resp.status_code == 422
        assert "DUCKLAKE_ENABLED" in resp.text
    finally:
        settings.ducklake_enabled = original


@pytest.mark.asyncio
async def test_an_iceberg_catalog_still_reports_its_own_kind(admin_client, workspace_factory):
    """The default path is untouched."""
    ws = await workspace_factory()
    slug = f"ice_{uuid.uuid4().hex[:8]}"
    resp = await admin_client.post(f"/workspaces/{ws['slug']}/catalogs", json={"name": slug})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "iceberg_polaris"
    assert body["polaris_name"] == slug
    assert body["metadata_schema"] is None
    assert body["capabilities"]["external_engine_readable"] is True
