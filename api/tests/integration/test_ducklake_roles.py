"""Each DuckLake catalog's role reaches its own schema and nothing else.

`tests/deploy/test_compose_ducklake.py` asserts the REVOKEs are written; this
asserts PostgreSQL enforces them, and that the per-catalog grants actually
isolate one catalog's metadata from another's. That isolation is the reason the
SQL denylist is no longer the only thing standing between an agent and every
catalog in the deployment.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from api.config import settings
from api.models.catalog import KIND_DUCKLAKE, Catalog
from api.models.storage_backend import StorageBackend
from api.models.user import Credential
from api.services.catalog_backends.ducklake import (
    DuckLakeCatalogBackend,
    agent_role_for,
    dispose_engine,
    get_engine,
    metadata_schema_for,
    new_role_password,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _ducklake_settings():
    url = os.getenv("DUCKLAKE_DATABASE_URL")
    if not url:
        pytest.skip("DUCKLAKE_DATABASE_URL not set; skipping DuckLake role test")
    original = (settings.ducklake_enabled, settings.ducklake_database_url)
    settings.ducklake_enabled = True
    settings.ducklake_database_url = url
    yield
    settings.ducklake_enabled, settings.ducklake_database_url = original


def _catalog(slug: str) -> Catalog:
    cat = Catalog(
        slug=slug, name=slug, kind=KIND_DUCKLAKE, metadata_schema=metadata_schema_for(slug)
    )
    cat.id = uuid.uuid4()
    cat.storage_backend = StorageBackend(kind="object_store", name="bundled", root_uri="")
    cat.pending_ducklake_password = new_role_password()
    cat.ducklake_credential = Credential(kind="ducklake_role", token=cat.pending_ducklake_password)
    return cat


def _role_url(catalog: Catalog, database: str) -> str:
    """The admin DUCKLAKE_DATABASE_URL with this catalog's role and a target db."""
    parts = urlsplit(settings.ducklake_database_url)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    role = agent_role_for(catalog.slug)
    password = catalog.ducklake_credential.token
    return urlunsplit(
        ("postgresql+asyncpg", f"{role}:{password}@{host}{port}", f"/{database}", "", "")
    )


async def _query(catalog: Catalog, database: str, sql: str):
    engine = create_async_engine(_role_url(catalog, database), poolclass=None)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar_one()
    finally:
        await engine.dispose()


@pytest.fixture
async def two_catalogs():
    """Two provisioned DuckLake catalogs, torn down afterwards."""
    backend = DuckLakeCatalogBackend()
    raw, curated = (
        _catalog(f"iso_a_{uuid.uuid4().hex[:6]}"),
        _catalog(f"iso_b_{uuid.uuid4().hex[:6]}"),
    )
    for cat in (raw, curated):
        await backend.ensure(cat)
        # A table to attempt to read across the boundary.
        async with get_engine().begin() as conn:
            await conn.execute(
                text(f'CREATE TABLE IF NOT EXISTS "{cat.metadata_schema}".probe (i int)')
            )
    yield raw, curated
    for cat in (raw, curated):
        async with get_engine().begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{cat.metadata_schema}" CASCADE'))
        await backend._drop_role(cat)
    await dispose_engine()


@pytest.mark.asyncio
async def test_a_catalogs_role_reads_its_own_metadata_schema(two_catalogs) -> None:
    raw, _ = two_catalogs
    assert await _query(raw, "ducklake", f'SELECT count(*) FROM "{raw.metadata_schema}".probe') == 0


@pytest.mark.asyncio
async def test_a_catalogs_role_cannot_read_another_catalogs_metadata(two_catalogs) -> None:
    """The point of per-catalog roles. If this ever passes, one compromised
    agent reaches every catalog's metadata in the deployment."""
    raw, curated = two_catalogs
    with pytest.raises((asyncpg.PostgresError, DBAPIError)) as excinfo:
        await _query(raw, "ducklake", f'SELECT count(*) FROM "{curated.metadata_schema}".probe')
    assert "permission denied" in str(excinfo.value).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("database", ["duckhaven", "polaris"])
async def test_a_catalogs_role_cannot_open_the_control_plane_databases(
    two_catalogs, database: str
) -> None:
    """If this ever passes, an agent can read the credentials table -- which is
    where every other catalog's password lives.

    Refused at connect time: asyncpg raises before SQLAlchemy can wrap it, so
    catch both.
    """
    raw, _ = two_catalogs
    with pytest.raises((asyncpg.PostgresError, DBAPIError)) as excinfo:
        await _query(raw, database, "SELECT 1")
    assert "permission denied" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_a_catalogs_role_cannot_create_in_the_public_schema(two_catalogs) -> None:
    """PostgreSQL grants CREATE on `public` to PUBLIC before version 15, so
    without the revoke a role could stage objects outside its own schema."""
    raw, _ = two_catalogs
    with pytest.raises((asyncpg.PostgresError, DBAPIError)):
        await _query(raw, "ducklake", "CREATE TABLE public.sneaky (i int)")


@pytest.mark.asyncio
async def test_a_catalogs_role_is_not_a_superuser(two_catalogs) -> None:
    """A superuser bypasses every grant above."""
    raw, _ = two_catalogs
    role = agent_role_for(raw.slug)
    assert (
        await _query(
            raw,
            "ducklake",
            f"SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles "
            f"WHERE rolname = '{role}'",
        )
        is False
    )


@pytest.mark.asyncio
async def test_rotating_a_role_invalidates_the_old_password(two_catalogs) -> None:
    """New dispatches must get a password that works, and the old one must stop."""
    raw, _ = two_catalogs
    old_password = raw.ducklake_credential.token

    raw.ducklake_credential.token = new_role_password()
    raw.pending_ducklake_password = None
    await DuckLakeCatalogBackend().ensure(raw)

    assert await _query(raw, "ducklake", "SELECT 1") == 1

    stale = _catalog(raw.slug)
    stale.ducklake_credential.token = old_password
    stale.pending_ducklake_password = None
    with pytest.raises((asyncpg.PostgresError, DBAPIError)):
        await _query(stale, "ducklake", "SELECT 1")
