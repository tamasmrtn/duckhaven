"""Unit tests for the system-catalog materializer: row mapping, the incremental
cursor, idempotency, and the info_schema snapshot. The PyIceberg writer is
substituted with an in-memory fake so these run without Polaris."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_workspace
from fake_polaris import FakePolaris
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.query import Query
from api.models.system_catalog import SystemCatalogSync
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.services.auth import hash_password
from api.services.system_catalog import tables
from api.services.system_catalog.materialize import (
    materialize_info_schema,
    materialize_query_history,
    run_cycle,
    statement_type,
)
from api.services.system_catalog.writer import IcebergSystemCatalogWriter, _to_arrow


class FakeWriter:
    def __init__(self) -> None:
        self.appended: dict[str, list[dict]] = defaultdict(list)
        self.overwritten: dict[str, list[dict]] = {}

    def ensure_table(self, table) -> None:  # noqa: D401, ANN001
        pass

    def append(self, table, rows) -> None:  # noqa: ANN001
        self.appended[table.name].extend(rows)

    def overwrite(self, table, rows) -> None:  # noqa: ANN001
        self.overwritten[table.name] = rows


@pytest.fixture
async def user(db_session) -> User:
    u = User(email="u@test.local", password_hash=hash_password("pw"), name="U", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _add_query(
    db_session, *, ws_id, user_id, sql="SELECT 1", started, finished, **kw
) -> Query:
    q = Query(
        workspace_id=ws_id,
        user_id=user_id,
        sql=sql,
        status=kw.get("status", "done"),
        origin=kw.get("origin"),
        row_count=kw.get("row_count"),
        result_bytes=kw.get("result_bytes"),
        duration_ms=kw.get("duration_ms"),
        error=kw.get("error"),
        profile=kw.get("profile"),
        started_at=started,
        finished_at=finished,
    )
    db_session.add(q)
    await db_session.commit()
    await db_session.refresh(q)
    return q


@pytest.mark.parametrize(
    "sql,expected",
    [("SELECT 1", "SELECT"), ("INSERT INTO t VALUES (1)", "INSERT"), ("not sql ;;", "UNKNOWN")],
)
def test_statement_type(sql, expected):
    assert statement_type(sql) == expected


async def test_history_maps_and_appends(db_session, user):
    ws, _ = await seed_workspace(db_session, user_id=user.id, slug="dev", name="Dev")
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    await _add_query(
        db_session,
        ws_id=ws.id,
        user_id=user.id,
        started=t0,
        finished=t0 + timedelta(seconds=2),
        row_count=5,
        duration_ms=2000,
        profile={"summary": {"reserved_memory_bytes": 1024, "reserved_threads": 4}},
    )
    writer = FakeWriter()
    copied = await materialize_query_history(db_session, writer)
    assert copied == 1

    [row] = writer.appended["history"]
    assert row["workspace_slug"] == "dev"
    assert row["user_email"] == "u@test.local"
    assert row["statement_type"] == "SELECT"
    assert row["row_count"] == 5
    assert row["reserved_memory_bytes"] == 1024
    assert row["reserved_threads"] == 4

    [audit] = writer.appended["audit"]
    assert audit["actor"] == "u@test.local"
    assert audit["action"] == "SELECT"
    assert audit["query_id"] == row["query_id"]


async def test_history_is_idempotent(db_session, user):
    ws, _ = await seed_workspace(db_session, user_id=user.id, slug="dev", name="Dev")
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    await _add_query(db_session, ws_id=ws.id, user_id=user.id, started=t0, finished=t0)
    writer = FakeWriter()
    assert await materialize_query_history(db_session, writer) == 1
    # Second run sees no new terminal queries past the cursor.
    assert await materialize_query_history(db_session, writer) == 0


async def test_history_skips_non_terminal(db_session, user):
    ws, _ = await seed_workspace(db_session, user_id=user.id, slug="dev", name="Dev")
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    await _add_query(
        db_session, ws_id=ws.id, user_id=user.id, started=t0, finished=None, status="running"
    )
    writer = FakeWriter()
    assert await materialize_query_history(db_session, writer) == 0


async def test_history_batch_then_drains(db_session, user):
    ws, _ = await seed_workspace(db_session, user_id=user.id, slug="dev", name="Dev")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    for i in range(3):
        await _add_query(
            db_session,
            ws_id=ws.id,
            user_id=user.id,
            started=base + timedelta(seconds=i),
            finished=base + timedelta(seconds=i),
        )
    writer = FakeWriter()
    assert await materialize_query_history(db_session, writer, batch_size=2) == 2
    assert await materialize_query_history(db_session, writer, batch_size=2) == 1
    assert await materialize_query_history(db_session, writer, batch_size=2) == 0
    assert len(writer.appended["history"]) == 3


async def test_info_schema_snapshot(db_session, user, fake_polaris: FakePolaris):
    ws, catalog = await seed_workspace(
        db_session, user_id=user.id, slug="dev", name="Dev", catalog_slug="dev_cat"
    )
    # One namespace + table in Polaris, with a metadata sidecar row.
    await fake_polaris.create_schema(catalog.polaris_name, "analytics")
    await fake_polaris.create_table(
        catalog=catalog.polaris_name,
        schema="analytics",
        name="orders",
        columns=[{"id": 1, "name": "id", "required": True, "type": "long"}],
    )
    db_session.add(
        TableMetadata(
            catalog_id=catalog.id,
            schema_name="analytics",
            table_name="orders",
            owner_id=user.id,
            row_count=42,
        )
    )
    await db_session.commit()

    writer = FakeWriter()
    await materialize_info_schema(db_session, fake_polaris, writer)

    assert {c["catalog"] for c in writer.overwritten["catalogs"]} == {"dev_cat"}
    assert {(t["table_name"], t["row_count"]) for t in writer.overwritten["tables"]} == {
        ("orders", 42)
    }
    assert writer.overwritten["tables"][0]["owner_email"] == "u@test.local"
    cols = writer.overwritten["columns"]
    assert {c["column_name"] for c in cols} == {"id"}


def test_table_specs_cover_all_namespaces():
    # Sanity: every declared table belongs to a known system namespace.
    from api.services.system_catalog.constants import SYSTEM_NAMESPACES

    assert {t.namespace for t in tables.ALL_TABLES} <= set(SYSTEM_NAMESPACES)


async def test_run_cycle_records_status(db_engine, user, fake_polaris):
    """A full cycle copies history and clears the error/timestamp on the sync row."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        ws, _ = await seed_workspace(db, user_id=user.id, slug="dev", name="Dev")
        t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        await _add_query(db, ws_id=ws.id, user_id=user.id, started=t0, finished=t0)

    writer = FakeWriter()
    result = await run_cycle(factory, fake_polaris, writer)
    assert result == {"status": "ran", "copied": 1}
    assert len(writer.appended["history"]) == 1

    async with factory() as db:
        sync = await db.get(SystemCatalogSync, 1)
        assert sync.last_error is None
        assert sync.last_run_at is not None


def test_to_arrow_builds_schema_typed_table():
    arrow = _to_arrow(tables.INFO_SCHEMAS, [{"catalog": "c", "schema_name": "s"}])
    assert arrow.schema == tables.INFO_SCHEMAS.schema
    assert arrow.to_pylist() == [{"catalog": "c", "schema_name": "s"}]
    # Empty rows yield a typed, empty table (used by overwrite of an empty snapshot).
    assert _to_arrow(tables.INFO_SCHEMAS, []).num_rows == 0


def test_writer_append_empty_is_noop():
    # No rows → returns before any catalog/network access (no connection built).
    writer = IcebergSystemCatalogWriter(
        base_url="http://unused", realm="POLARIS", client_id="x", client_secret="y"
    )
    writer.append(tables.QUERY_HISTORY, [])
    assert writer._catalog is None
