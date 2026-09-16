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
    CatalogBackendConflict,
    CatalogBackendError,
    CatalogBackendNotFound,
    CatalogBackendUnavailable,
)
from api.services.catalog_backends.ducklake import (
    _TYPE_TO_DUCKDB,
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


def _dbapi_error(sqlstate: str | None) -> Exception:
    """A SQLAlchemy-shaped wrapper around a driver error carrying a SQLSTATE."""
    orig = type("PGError", (Exception,), {"sqlstate": sqlstate})()
    return type("DBAPIError", (Exception,), {"orig": orig})()


def test_missing_relation_is_recognised_but_other_failures_are_not():
    """A never-attached catalog reads as empty; a real failure must still raise.

    Keyed on SQLSTATE rather than the driver's exception repr, which is not part
    of asyncpg's contract — matching the repr would silently become "502 on every
    browse" if it changed.
    """
    assert _is_missing_relation(_dbapi_error("42P01")) is True  # undefined_table
    assert _is_missing_relation(_dbapi_error("3F000")) is True  # invalid_schema_name
    assert _is_missing_relation(_dbapi_error("42501")) is False  # insufficient_privilege
    assert _is_missing_relation(_dbapi_error(None)) is False
    assert _is_missing_relation(Exception("connection refused")) is False
    # A driver exception raised directly, not wrapped.
    bare = type("PGError", (Exception,), {"sqlstate": "42P01"})()
    assert _is_missing_relation(bare) is True


def test_capabilities_are_stated_honestly():
    caps = DUCKLAKE_CAPABILITIES
    assert caps.snapshot_granularity == "catalog"
    assert caps.external_engine_readable is False
    assert caps.maintenance_executable is True
    assert caps.supports_storage_migration is False


def test_every_offered_column_type_is_representable_in_ducklake():
    """The reason no type check is needed on the create path: AllowedColumnType
    is a Literal of eight scalars and DuckLake can represent all of them."""
    from api.schemas.catalog import AllowedColumnType

    assert set(AllowedColumnType.__args__) <= set(_TYPE_TO_DUCKDB)


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
async def test_cascade_drops_a_schema_in_one_statement(recording):
    """Each DuckLake DDL is a round-trip to an agent, so dropping a 50-table
    schema table by table would be 50 sequential dispatches on one request."""
    await DuckLakeCatalogBackend().delete_schema(_catalog(), "staging", _ctx(), cascade=True)
    assert recording.sql == ['DROP SCHEMA "raw"."staging" CASCADE']


@pytest.mark.asyncio
async def test_no_agent_explains_why_one_is_needed(monkeypatch):
    _patch_query_service(monkeypatch, _RecordingQueryService(agent=None))
    with pytest.raises(CatalogBackendUnavailable, match="runs on an agent"):
        await DuckLakeCatalogBackend().create_schema(_catalog(), "staging", _ctx())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("Schema with name staging already exists", CatalogBackendConflict),
        ("Table with name orders does not exist", CatalogBackendNotFound),
        ("connection reset by peer", CatalogBackendUnavailable),
    ],
)
async def test_agent_failures_map_to_the_seam_vocabulary(monkeypatch, error, expected):
    """So a DuckLake DDL failure produces the same HTTP status a Polaris one
    would: 409 for a conflict, 404 for a missing object, 502 otherwise."""
    _patch_query_service(monkeypatch, _RecordingQueryService(status="failed", error=error))
    with pytest.raises(expected):
        await DuckLakeCatalogBackend().create_schema(_catalog(), "staging", _ctx())


# --- Snapshot derivation and the purge contract -----------------------------


class _FakeRows:
    """Stands in for the catalog database, capturing the SQL it is asked for."""

    def __init__(self, rows: list) -> None:
        self.rows, self.sql = rows, []

    async def __call__(self, catalog, sql, params):  # noqa: ANN001
        self.sql.append(" ".join(sql.split()))
        return self.rows


@pytest.mark.asyncio
async def test_snapshot_derivation_covers_files_deletes_and_the_table_itself():
    """A DuckLake snapshot is a catalog commit, so a table's history is derived.
    Each source alone misses real history: without ducklake_table a table created
    empty has none, and without ducklake_delete_file a delete is invisible."""
    from datetime import UTC, datetime

    backend = DuckLakeCatalogBackend()
    rows = _FakeRows(
        [
            (7, datetime(2026, 9, 1, 12, 0, tzinfo=UTC), 2),
            (4, datetime(2026, 8, 1, 12, 0, tzinfo=UTC), 1),
        ]
    )
    backend._rows = rows  # type: ignore[method-assign]
    got = await backend.list_snapshots(_catalog(), "analytics", "t")

    sql = rows.sql[0]
    assert "ducklake_table" in sql
    assert "ducklake_data_file" in sql
    assert "ducklake_delete_file" in sql
    # Newest first, and only the newest is current.
    assert [s.snapshot_id for s in got] == [7, 4]
    assert [s.is_current for s in got] == [True, False]
    # Labelled for what these actually are.
    assert all(s.granularity == "catalog" for s in got)


@pytest.mark.asyncio
async def test_a_table_with_no_history_returns_nothing_rather_than_failing():
    backend = DuckLakeCatalogBackend()
    backend._rows = _FakeRows([])  # type: ignore[method-assign]
    assert await backend.list_snapshots(_catalog(), "analytics", "t") == []


def test_naive_snapshot_timestamps_are_read_as_utc():
    """`.timestamp()` on a naive datetime reads it as *local* time, which would
    shift every snapshot by the server's UTC offset."""
    from datetime import UTC, datetime

    from api.services.catalog_backends.ducklake import _epoch_ms

    aware = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 1, 12, 0)
    assert _epoch_ms(naive) == _epoch_ms(aware)


@pytest.mark.asyncio
async def test_a_purge_failure_never_blocks_the_drop(monkeypatch, caplog):
    """Best-effort by contract: a storage failure must leave orphaned objects and
    a loud log, not a catalog that can never be dropped."""
    import api.services.catalog_backends.ducklake as mod
    import api.services.session_credentials as creds

    def _boom(*a, **k):
        raise RuntimeError("object store unreachable")

    monkeypatch.setattr(mod, "_purge_s3_prefix", _boom)
    # `_purge_data` imports these lazily, so they are patched at their source.
    monkeypatch.setattr(creds, "build_storage_block", lambda *a, **k: {"type": "s3"})
    monkeypatch.setattr(creds, "ducklake_data_path", lambda c: "s3://w/raw/")

    from api.models.storage_backend import StorageBackend

    cat = _catalog()
    cat.storage_backend = StorageBackend(kind="object_store", name="b", root_uri="")
    with caplog.at_level("WARNING"):
        await DuckLakeCatalogBackend()._purge_data(cat)
    assert "orphaned" in caplog.text
