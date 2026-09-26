"""The API reads a DuckLake catalog's format from its real metadata tables.

The format decides which runtimes may attach the catalog (see
``api.services.runtimes``), and it only exists once the extension has created the
catalog's tables on a first ATTACH — which this test performs with the API's own
DuckDB, the default runtime's line.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit

import duckdb
import pytest
from sqlalchemy import text

from api.config import settings
from api.models.catalog import KIND_DUCKLAKE, Catalog
from api.services.catalog_backends import ducklake
from duckhaven_shared.runtimes import DEFAULT_RUNTIME_ID, RUNTIMES

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _ducklake_settings():
    url = os.getenv("DUCKLAKE_DATABASE_URL")
    if not url:
        pytest.skip("DUCKLAKE_DATABASE_URL not set; skipping DuckLake format test")
    original = (settings.ducklake_enabled, settings.ducklake_database_url)
    settings.ducklake_enabled = True
    settings.ducklake_database_url = url
    yield
    settings.ducklake_enabled, settings.ducklake_database_url = original


def _first_attach(schema: str, data_path: str) -> None:
    """Create the catalog's tables the way an agent's first ATTACH does."""
    parts = urlsplit(settings.ducklake_database_url)
    conn = duckdb.connect()
    conn.execute("LOAD ducklake")
    conn.execute("LOAD postgres")
    conn.execute(
        f"CREATE SECRET meta (TYPE POSTGRES, HOST '{parts.hostname}', PORT {parts.port or 5432}, "
        f"DATABASE '{parts.path.lstrip('/')}', USER '{parts.username}', "
        f"PASSWORD '{parts.password}')"
    )
    conn.execute(
        f"ATTACH 'ducklake:postgres:dbname={parts.path.lstrip('/')}' AS lake "
        f"(DATA_PATH '{data_path}', METADATA_SCHEMA '{schema}', META_SECRET 'meta', "
        "CREATE_IF_NOT_EXISTS true)"
    )
    conn.close()


async def test_a_catalog_has_no_format_until_first_attach_then_the_default_runtimes(tmp_path):
    slug = f"fmt_{uuid.uuid4().hex[:8]}"
    catalog = Catalog(slug=slug, name=slug, kind=KIND_DUCKLAKE, metadata_schema=slug)
    catalog.id = uuid.uuid4()
    try:
        assert await ducklake.catalog_format(catalog) is None

        _first_attach(slug, f"{tmp_path}/")

        assert (
            await ducklake.catalog_format(catalog) == RUNTIMES[DEFAULT_RUNTIME_ID].ducklake_format
        )
        assert await ducklake.catalog_formats([catalog]) == {slug: "1.0"}
    finally:
        async with ducklake.get_engine().begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{slug}" CASCADE'))
        await ducklake.dispose_engine()
