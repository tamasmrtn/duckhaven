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


# --- DDL generation ---------------------------------------------------------
# DuckLake DDL runs on an agent, so what is asserted here is the SQL the backend
# emits and how it classifies the agent's answer. The statements are executed for
# real against a live catalog in the integration suite.


class _RecordingQueryService:
    """Stands in for services.query, capturing the dispatched SQL."""

    def __init__(self, status: str = "done", error: str | None = None, agent: object = True):
        self.sql: list[str] = []
        self.origin: list[str | None] = []
        self._status, self._error, self._agent = status, error, agent

    async def pick_agent_for(self, db, workspace, *, principal_id=None):  # noqa: ANN001
        return self._agent

    async def run_sync_query(self, db, **kwargs):  # noqa: ANN001
        self.sql.append(kwargs["sql"])
        self.origin.append(kwargs.get("origin"))
        return type("Q", (), {"status": self._status, "error": self._error})()


def _patch_query_service(monkeypatch, svc: _RecordingQueryService) -> _RecordingQueryService:
    """`_run_ddl` does `from api.services import query`, which resolves the
    attribute on the package — so that is what has to be replaced."""
    import api.services

    monkeypatch.setattr(api.services, "query", svc, raising=False)
    return svc


@pytest.fixture
def recording(monkeypatch):
    return _patch_query_service(monkeypatch, _RecordingQueryService())


def _ctx():
    from api.services.catalog_backends import WriteContext

    return WriteContext(workspace=object(), user=type("U", (), {"id": "u1"})(), db=object())


@pytest.mark.asyncio
async def test_create_schema_sql(recording):
    await DuckLakeCatalogBackend().create_schema(_catalog(), "staging", _ctx())
    assert recording.sql == [
        'CREATE SCHEMA "cat_raw_slug_placeholder"."staging"'.replace(
            "cat_raw_slug_placeholder", "raw"
        )
    ]
    # Metadata DDL is kept out of the user's query history.
    assert recording.origin == ["metadata"]


@pytest.mark.asyncio
async def test_create_table_sql_carries_types_and_nullability(recording):
    from api.schemas.catalog import ColumnSpec

    backend = DuckLakeCatalogBackend()
    columns = [
        ColumnSpec(name="id", type="BIGINT", nullable=False),
        ColumnSpec(name="label", type="VARCHAR", nullable=True),
        ColumnSpec(name="amount", type="DECIMAL", nullable=True),
    ]

    # get_table is called afterwards to read the result back; short-circuit it.
    async def _get_table(*a, **k):
        return None

    backend.get_table = _get_table  # type: ignore[method-assign]
    await backend.create_table(_catalog(), "staging", "orders", columns, _ctx())
    assert recording.sql == [
        'CREATE TABLE "raw"."staging"."orders" '
        '("id" BIGINT NOT NULL, "label" VARCHAR, "amount" DECIMAL(38,9))'
    ]


@pytest.mark.asyncio
async def test_drop_statements(recording):
    backend = DuckLakeCatalogBackend()
    await backend.delete_table(_catalog(), "staging", "orders", _ctx())
    await backend.delete_schema(_catalog(), "staging", _ctx())
    assert recording.sql == [
        'DROP TABLE "raw"."staging"."orders"',
        'DROP SCHEMA "raw"."staging"',
    ]


@pytest.mark.asyncio
async def test_unsupported_column_types_are_refused_before_dispatch(recording):
    """Rejected at the API with a clear message rather than mid-DDL on an agent."""
    from api.services.catalog_backends import CatalogBackendBadRequest

    spec = type("Spec", (), {"name": "c", "type": "ARRAY", "nullable": True})()
    with pytest.raises(CatalogBackendBadRequest, match="ARRAY"):
        await DuckLakeCatalogBackend().create_table(_catalog(), "s", "t", [spec], _ctx())
    assert recording.sql == []


@pytest.mark.asyncio
async def test_no_agent_explains_why_one_is_needed(monkeypatch):
    _patch_query_service(monkeypatch, _RecordingQueryService(agent=None))
    with pytest.raises(CatalogBackendUnavailable, match="runs on an agent"):
        await DuckLakeCatalogBackend().create_schema(_catalog(), "staging", _ctx())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_name"),
    [
        ("Schema with name staging already exists", "CatalogBackendConflict"),
        ("Table with name orders does not exist", "CatalogBackendNotFound"),
        ("connection reset by peer", "CatalogBackendUnavailable"),
    ],
)
async def test_agent_failures_map_to_the_seam_vocabulary(monkeypatch, error, expected_name):
    """So a DuckLake DDL failure produces the same HTTP status a Polaris one
    would: 409 for a conflict, 404 for a missing object, 502 otherwise."""
    _patch_query_service(monkeypatch, _RecordingQueryService(status="failed", error=error))
    with pytest.raises(Exception) as excinfo:
        await DuckLakeCatalogBackend().create_schema(_catalog(), "staging", _ctx())
    assert type(excinfo.value).__name__ == expected_name
