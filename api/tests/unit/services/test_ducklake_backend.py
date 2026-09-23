"""The DuckLake backend's pure logic: naming, quoting, capabilities, gating.

Anything needing a live catalog database lives in
`api/tests/integration/test_ducklake_catalog.py`.
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
    """Postgres truncates at 63 characters, leaving a schema that does not exist."""
    assert len(metadata_schema_for("a" * 59)) == 63
    with pytest.raises(CatalogBackendError, match="63-character"):
        metadata_schema_for("a" * 60)


def test_quote_doubles_embedded_quotes():
    assert _quote("cat_raw") == '"cat_raw"'
    assert _quote('we"ird') == '"we""ird"'


@pytest.mark.asyncio
async def test_operations_are_refused_while_ducklake_is_disabled():
    """Off by default: refused up front rather than failing with a connection error."""
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
    """Drop is idempotent, matching the Polaris backend's NotFound tolerance."""
    await DuckLakeCatalogBackend().deprovision(
        Catalog(slug="raw", name="raw", kind=KIND_DUCKLAKE, metadata_schema=None)
    )


def _dbapi_error(sqlstate: str | None) -> Exception:
    """A SQLAlchemy-shaped wrapper around a driver error carrying a SQLSTATE."""
    orig = type("PGError", (Exception,), {"sqlstate": sqlstate})()
    return type("DBAPIError", (Exception,), {"orig": orig})()


def test_missing_relation_is_recognised_but_other_failures_are_not():
    """A never-attached catalog reads empty; a real failure still raises.

    Keyed on SQLSTATE: asyncpg does not guarantee its exception repr.
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
    # The trade-off a user makes when choosing DuckLake, and the one thing it
    # does that Iceberg cannot.
    assert caps.external_engine_readable is False
    assert caps.supports_maintenance_apply is True
    # Relocating is a prefix copy plus one row, not a metadata-tree rewrite.
    assert caps.supports_storage_migration is True


def test_every_offered_column_type_is_representable_in_ducklake():
    """AllowedColumnType is a Literal of eight scalars, all representable."""
    from api.schemas.catalog import AllowedColumnType

    assert set(AllowedColumnType.__args__) <= set(_TYPE_TO_DUCKDB)


# --- DDL generation ---------------------------------------------------------
# Asserts the SQL the backend emits and how it classifies the agent's answer;
# the integration suite executes the statements for real.


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
    """`_run_ddl` resolves `api.services.query`, so patch it on the package."""
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
    # Metadata DDL stays out of the user's query history.
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

    # get_table reads the result back; short-circuit it.
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
    """DuckLake DDL is one agent round-trip per statement, so cascade does 50
    tables in one rather than 50 dispatches on one request."""
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
    """A DuckLake DDL failure maps to the same HTTP status as a Polaris one."""
    _patch_query_service(monkeypatch, _RecordingQueryService(status="failed", error=error))
    with pytest.raises(expected):
        await DuckLakeCatalogBackend().create_schema(_catalog(), "staging", _ctx())


# --- Snapshot derivation and the purge contract -----------------------------


class _FakeRows:
    """Stands in for the catalog database, capturing the SQL it is asked for."""

    def __init__(self, rows: list) -> None:
        self.rows, self.sql, self.operations = rows, [], []

    async def __call__(self, catalog, sql, params, *, operation="read"):  # noqa: ANN001
        self.sql.append(" ".join(sql.split()))
        self.operations.append(operation)
        return self.rows


@pytest.mark.asyncio
async def test_snapshot_derivation_covers_files_deletes_and_the_table_itself():
    """History unions the table, data-file, delete-file and inlined-change sources."""
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
    assert "ducklake_snapshot_changes" in sql
    # Newest first; only the newest is current, and all are catalog-granularity.
    assert [s.snapshot_id for s in got] == [7, 4]
    assert [s.is_current for s in got] == [True, False]
    assert all(s.granularity == "catalog" for s in got)


@pytest.mark.asyncio
async def test_a_table_with_no_history_returns_nothing_rather_than_failing():
    backend = DuckLakeCatalogBackend()
    backend._rows = _FakeRows([])  # type: ignore[method-assign]
    assert await backend.list_snapshots(_catalog(), "analytics", "t") == []


def test_naive_snapshot_timestamps_are_read_as_utc():
    """A naive datetime read as local time would shift every snapshot."""
    from datetime import UTC, datetime

    from api.services.catalog_backends.ducklake import _epoch_ms

    aware = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 1, 12, 0)
    assert _epoch_ms(naive) == _epoch_ms(aware)


@pytest.mark.asyncio
async def test_a_purge_failure_never_blocks_the_drop(monkeypatch, caplog):
    """A storage failure leaves orphaned objects and a loud log, not an
    un-droppable catalog."""
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


@pytest.mark.asyncio
async def test_inlined_writes_are_matched_a_token_at_a_time():
    """A substring match for table 1 would also match table 13; `_` is escaped."""
    backend = DuckLakeCatalogBackend()
    rows = _FakeRows([])
    backend._rows = rows  # type: ignore[method-assign]
    await backend.list_snapshots(_catalog(), "analytics", "t")

    sql = rows.sql[0]
    assert "string_to_array(c.changes_made, ',')" in sql
    assert "split_part(tok, ':', 2) = tbl.table_id::text" in sql
    assert "ESCAPE" in sql


@pytest.mark.asyncio
async def test_a_table_carries_the_size_the_catalog_already_knows():
    """ducklake_table_stats' byte total is surfaced without an agent probe."""
    backend = DuckLakeCatalogBackend()
    backend._rows = _FakeRows(  # type: ignore[method-assign]
        [("events", "uuid-1", 5000, 20994)]
    )
    tables = await backend.list_tables(_catalog(), "analytics")

    assert [t.size_bytes for t in tables] == [20994]


@pytest.mark.asyncio
async def test_a_table_with_no_stats_row_reports_no_size():
    """A table written but never stat-ted; None, not a confident zero."""
    backend = DuckLakeCatalogBackend()
    backend._rows = _FakeRows([("events", "uuid-1", None, None)])  # type: ignore[method-assign]
    tables = await backend.list_tables(_catalog(), "analytics")

    assert tables[0].size_bytes is None


@pytest.mark.asyncio
async def test_each_metadata_read_is_named_for_its_metric_label():
    """The operation label is a fixed name, keeping label cardinality bounded."""
    backend = DuckLakeCatalogBackend()
    rows = _FakeRows([])
    backend._rows = rows  # type: ignore[method-assign]

    await backend.list_schemas(_catalog())
    await backend.list_tables(_catalog(), "analytics")
    await backend.list_snapshots(_catalog(), "analytics", "t")

    assert rows.operations == ["list_schemas", "list_tables", "list_snapshots"]


# --- Error taxonomy ---------------------------------------------------------
# DuckLake DDL errors come back as text, so the seam's exception is chosen by
# matching it. Real messages captured from DuckDB 1.5.5; a missing schema says
# "not found", not "does not exist".


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('Catalog Error: Schema with name "analytics" already exists!', CatalogBackendConflict),
        ('Catalog Error: Table with name "events" already exists!', CatalogBackendConflict),
        ("Catalog Error: Table with name nope does not exist!", CatalogBackendNotFound),
        (
            'Binder Error: Schema "nope" not found in DuckLakeCatalog "lake"',
            CatalogBackendNotFound,
        ),
        ("IO Error: could not reach the object store", CatalogBackendUnavailable),
    ],
)
@pytest.mark.asyncio
async def test_a_real_duckdb_failure_maps_to_the_right_seam_error(message, expected, monkeypatch):
    """Drives `_run_ddl` with messages DuckDB actually emits."""
    from types import SimpleNamespace

    from api.services import query as query_service

    async def _agent(db, workspace, *, principal_id=None):  # noqa: ANN001
        return SimpleNamespace(id="agent-1")

    async def _run(db, **kwargs):  # noqa: ANN001
        return SimpleNamespace(status="failed", error=message)

    monkeypatch.setattr(query_service, "pick_agent_for", _agent)
    monkeypatch.setattr(query_service, "run_sync_query", _run)

    ctx = SimpleNamespace(db=None, workspace=SimpleNamespace(id="ws"), user=SimpleNamespace(id="u"))
    with pytest.raises(expected):
        await DuckLakeCatalogBackend()._run_ddl(_catalog(), "CREATE SCHEMA x", ctx, what="create")


@pytest.mark.asyncio
async def test_ddl_with_no_agent_says_why_rather_than_timing_out(monkeypatch):
    """A DuckLake catalog can be browsed with no compute, but not changed --
    only the extension can commit its DDL. The 503 should explain that."""
    from types import SimpleNamespace

    from api.services import query as query_service

    async def _none(db, workspace, *, principal_id=None):  # noqa: ANN001
        return None

    monkeypatch.setattr(query_service, "pick_agent_for", _none)

    ctx = SimpleNamespace(db=None, workspace=SimpleNamespace(id="ws"), user=SimpleNamespace(id="u"))
    with pytest.raises(CatalogBackendUnavailable, match="only the DuckLake extension"):
        await DuckLakeCatalogBackend()._run_ddl(_catalog(), "CREATE SCHEMA x", ctx, what="create")
