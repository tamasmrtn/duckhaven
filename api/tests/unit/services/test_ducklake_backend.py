"""The DuckLake backend's pure logic: naming, quoting, capabilities, gating.

Anything needing a live catalog database is in
`api/tests/integration/test_ducklake_catalog.py`; what is here runs with no
infrastructure.
"""

from __future__ import annotations

import pytest

from api.config import settings
from api.models.catalog import KIND_DUCKLAKE, Catalog
from api.services.catalog_backends import (
    CatalogBackendError,
    CatalogBackendUnavailable,
)
from api.services.catalog_backends.ducklake import (
    DUCKLAKE_CAPABILITIES,
    DuckLakeCatalogBackend,
    _is_missing_relation,
    _quote,
    metadata_schema_for,
)


def _catalog(slug: str = "raw") -> Catalog:
    return Catalog(
        slug=slug, name=slug, kind=KIND_DUCKLAKE, metadata_schema=metadata_schema_for(slug)
    )


def test_metadata_schema_is_derived_from_the_slug():
    assert metadata_schema_for("raw") == "cat_raw"
    assert metadata_schema_for("my_catalog_2") == "cat_my_catalog_2"


def test_metadata_schema_refuses_to_exceed_the_postgres_identifier_limit():
    """Postgres silently truncates at 63 characters, which would leave the row
    pointing at a schema name that does not exist."""
    assert len(metadata_schema_for("a" * 59)) == 63
    with pytest.raises(CatalogBackendError, match="63-character"):
        metadata_schema_for("a" * 60)


def test_quote_doubles_embedded_quotes():
    assert _quote("cat_raw") == '"cat_raw"'
    assert _quote('we"ird') == '"we""ird"'


@pytest.mark.asyncio
async def test_operations_are_refused_while_ducklake_is_disabled():
    """Off by default. A catalog of this kind cannot be reached until an
    operator turns it on, rather than failing later with a connection error."""
    original = settings.ducklake_enabled
    settings.ducklake_enabled = False
    try:
        with pytest.raises(CatalogBackendUnavailable, match="DUCKLAKE_ENABLED"):
            await DuckLakeCatalogBackend().ensure(_catalog())
    finally:
        settings.ducklake_enabled = original


@pytest.mark.asyncio
async def test_a_catalog_without_a_metadata_schema_is_an_error_not_a_crash():
    original = settings.ducklake_enabled
    settings.ducklake_enabled = True
    try:
        broken = Catalog(slug="raw", name="raw", kind=KIND_DUCKLAKE, metadata_schema=None)
        with pytest.raises(CatalogBackendError, match="metadata schema"):
            await DuckLakeCatalogBackend().ensure(broken)
    finally:
        settings.ducklake_enabled = original


@pytest.mark.asyncio
async def test_deprovisioning_a_catalog_with_no_schema_is_a_no_op():
    """Drop must stay idempotent so a partially-provisioned catalog still
    deletes cleanly, matching the Polaris backend's NotFound tolerance."""
    await DuckLakeCatalogBackend().deprovision(
        Catalog(slug="raw", name="raw", kind=KIND_DUCKLAKE, metadata_schema=None)
    )


def test_missing_relation_is_recognised_but_other_failures_are_not():
    """A never-attached catalog reads as empty; a real failure must still raise."""
    assert _is_missing_relation(Exception("asyncpg.exceptions.UndefinedTableError: nope")) is True
    assert (
        _is_missing_relation(Exception("asyncpg.exceptions.InvalidSchemaNameError: nope")) is True
    )
    assert _is_missing_relation(Exception("connection refused")) is False
    assert _is_missing_relation(Exception("permission denied for schema cat_raw")) is False


def test_capabilities_are_stated_honestly():
    caps = DUCKLAKE_CAPABILITIES
    assert caps.snapshot_granularity == "catalog"
    assert caps.external_engine_readable is False
    assert caps.maintenance_executable is True
    assert caps.supports_storage_migration is False


def test_unsupported_column_types_match_the_ducklake_specification():
    """Rejected at the API so a create fails with a clear message rather than
    mid-DDL on an agent. Source: ducklake.select unsupported-features list."""
    assert DUCKLAKE_CAPABILITIES.unsupported_column_types == frozenset(
        {"ARRAY", "ENUM", "UNION", "VARINT", "BITSTRING"}
    )
    # Everything DuckHaven's create-table dialog offers must be representable.
    from api.schemas.catalog import AllowedColumnType

    offered = set(AllowedColumnType.__args__)
    assert not (offered & DUCKLAKE_CAPABILITIES.unsupported_column_types)
