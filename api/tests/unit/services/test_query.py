"""Unit tests for query-service helpers added for catalog stats + JSON rows."""

from __future__ import annotations

import os
import tempfile

import duckdb
import pytest
from conftest import seed_workspace
from sqlalchemy import select

from api.models.agent import Agent
from api.models.query import Query
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from duckhaven_shared.protocol import Frame, FrameType


def _parquet_bytes(sql: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
        path = fh.name
    try:
        duckdb.connect().execute(f"COPY ({sql}) TO '{path}' (FORMAT PARQUET)")
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


def test_decode_parquet_page_paginates():
    content = _parquet_bytes("SELECT * FROM range(5) t(n)")
    rows, columns = query_service.decode_parquet_page(content, limit=2, offset=2)
    assert columns == ["n"]
    assert rows == [{"n": 2}, {"n": 3}]


def test_decode_parquet_page_json_coerces_types():
    content = _parquet_bytes("SELECT DATE '2026-01-02' AS d, 1.5::DECIMAL(4,2) AS amt")
    rows, _ = query_service.decode_parquet_page(content, limit=10, offset=0)
    assert rows == [{"d": "2026-01-02", "amt": 1.5}]


def test_decode_parquet_page_handles_timestamptz():
    # DuckDB materializes a TIMESTAMP WITH TIME ZONE cell via pytz; without that
    # dep fetchall() raises and the rows endpoint 500s (issue #162).
    content = _parquet_bytes("SELECT TIMESTAMPTZ '2026-01-02 03:04:05+00' AS ts")
    rows, columns = query_service.decode_parquet_page(content, limit=10, offset=0)
    assert columns == ["ts"]
    assert rows[0]["ts"].startswith("2026-01-02")


async def _make_workspace(db_session):
    user = User(email="svc@test.local", password_hash=hash_password("pw"), name="Svc", role="user")
    db_session.add(user)
    await db_session.flush()
    return await seed_workspace(db_session, user_id=user.id, slug="svc-ws", name="Svc WS")


async def _queued_query(db_session, ws, **kw):
    query = Query(workspace_id=ws.id, sql="SELECT 1", status="queued", **kw)
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)
    return query


async def test_progress_stamps_running_at(db_session):
    ws, _ = await _make_workspace(db_session)
    query = await _queued_query(db_session, ws)

    await query_service.handle_agent_frame(
        db_session,
        Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id)}),
    )

    await db_session.refresh(query)
    assert query.status == "running"
    assert query.running_at is not None


async def test_progress_stamps_running_at_for_every_origin(db_session):
    """The queue-wait histogram is interactive-only; the column is not.

    A scheduled or session run's queue wait is just as worth showing in the
    history table's duration breakdown.
    """
    ws, _ = await _make_workspace(db_session)
    query = await _queued_query(db_session, ws, origin="scheduled")

    await query_service.handle_agent_frame(
        db_session,
        Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id)}),
    )

    await db_session.refresh(query)
    assert query.running_at is not None


async def test_a_second_progress_frame_does_not_move_running_at(db_session):
    ws, _ = await _make_workspace(db_session)
    query = await _queued_query(db_session, ws)
    progress = Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id)})

    await query_service.handle_agent_frame(db_session, progress)
    await db_session.refresh(query)
    first = query.running_at

    await query_service.handle_agent_frame(db_session, progress)
    await db_session.refresh(query)
    assert query.running_at == first


async def test_done_backs_running_at_out_of_duration_when_progress_never_arrived(db_session):
    """A fast query reaches QUERY_DONE without ever emitting QUERY_PROGRESS.

    Leaving running_at null there would report the whole wall-clock as queue wait.
    """
    ws, _ = await _make_workspace(db_session)
    query = await _queued_query(db_session, ws)

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": str(query.id), "status": "done", "duration_ms": 2000},
        ),
    )

    await db_session.refresh(query)
    assert query.running_at is not None
    elapsed = (query.finished_at - query.running_at).total_seconds()
    assert elapsed == pytest.approx(2.0, abs=0.1)


async def test_done_falls_back_to_finished_at_without_a_duration(db_session):
    ws, _ = await _make_workspace(db_session)
    query = await _queued_query(db_session, ws)

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": str(query.id), "status": "failed", "error": "boom"},
        ),
    )

    await db_session.refresh(query)
    assert query.running_at == query.finished_at


async def test_done_preserves_a_running_at_already_stamped(db_session):
    ws, _ = await _make_workspace(db_session)
    query = await _queued_query(db_session, ws)
    await query_service.handle_agent_frame(
        db_session,
        Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id)}),
    )
    await db_session.refresh(query)
    stamped = query.running_at

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": str(query.id), "status": "done", "duration_ms": 5},
        ),
    )

    await db_session.refresh(query)
    assert query.running_at == stamped


async def test_query_done_upserts_table_stats(db_session):
    ws, catalog = await _make_workspace(db_session)
    query = Query(workspace_id=ws.id, sql="SELECT 1", status="running", origin="sample")
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    frame = Frame(
        type=FrameType.QUERY_DONE,
        payload={
            "query_id": str(query.id),
            "status": "done",
            "row_count": 20,
            "stats_table": {"catalog": catalog.slug, "schema": "main", "table": "events"},
            "table_row_count": 42,
            "table_size_bytes": None,
        },
    )
    await query_service.handle_agent_frame(db_session, frame)

    meta = (
        await db_session.execute(
            select(TableMetadata).where(
                TableMetadata.catalog_id == catalog.id,
                TableMetadata.schema_name == "main",
                TableMetadata.table_name == "events",
            )
        )
    ).scalar_one()
    assert meta.row_count == 42
    assert meta.size_bytes is None


async def test_query_done_persists_profile(db_session):
    ws, _catalog = await _make_workspace(db_session)
    query = Query(workspace_id=ws.id, sql="SELECT 1", status="running")
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    profile = {
        "summary": {"latency_ms": 12.0, "peak_memory_bytes": 1024, "spill_bytes": 0},
        "tree": {"type": "PROJECTION", "children": []},
    }
    frame = Frame(
        type=FrameType.QUERY_DONE,
        payload={"query_id": str(query.id), "status": "done", "profile": profile},
    )
    await query_service.handle_agent_frame(db_session, frame)
    await db_session.refresh(query)
    assert query.profile == profile


async def test_query_done_persists_result_schema(db_session):
    ws, _catalog = await _make_workspace(db_session)
    query = Query(workspace_id=ws.id, sql="SELECT 1", status="running")
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    schema = [
        {"name": "ts", "type": "TIMESTAMP WITH TIME ZONE"},
        {"name": "amt", "type": "DECIMAL(38,10)"},
    ]
    frame = Frame(
        type=FrameType.QUERY_DONE,
        payload={"query_id": str(query.id), "status": "done", "result_schema": schema},
    )
    await query_service.handle_agent_frame(db_session, frame)
    await db_session.refresh(query)
    assert query.result_schema == schema


async def test_query_done_without_result_schema_leaves_it_null(db_session):
    """An agent that predates the field reports nothing; the API stores nothing
    rather than deriving a schema from the (lossy) result Parquet."""
    ws, _catalog = await _make_workspace(db_session)
    query = Query(workspace_id=ws.id, sql="SELECT 1", status="running")
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    frame = Frame(
        type=FrameType.QUERY_DONE,
        payload={"query_id": str(query.id), "status": "done", "row_count": 1},
    )
    await query_service.handle_agent_frame(db_session, frame)
    await db_session.refresh(query)
    assert query.result_schema is None


async def test_session_statement_done_records_statement_metric(db_session):
    from api.config import settings
    from api.metrics import SQL_STATEMENTS

    ws, _catalog = await _make_workspace(db_session)
    query = Query(workspace_id=ws.id, sql="SELECT 1", status="running", origin="session")
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    before = SQL_STATEMENTS.labels(settings.replica_id, "done")._value.get()
    frame = Frame(
        type=FrameType.QUERY_DONE,
        payload={"query_id": str(query.id), "status": "done", "row_count": 1},
    )
    await query_service.handle_agent_frame(db_session, frame)
    await db_session.refresh(query)
    assert query.status == "done"
    assert SQL_STATEMENTS.labels(settings.replica_id, "done")._value.get() == before + 1


async def test_query_done_without_profile_stays_null(db_session):
    ws, _catalog = await _make_workspace(db_session)
    query = Query(workspace_id=ws.id, sql="CREATE TABLE t (x INT)", status="running")
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    frame = Frame(
        type=FrameType.QUERY_DONE,
        payload={"query_id": str(query.id), "status": "done"},
    )
    await query_service.handle_agent_frame(db_session, frame)
    await db_session.refresh(query)
    assert query.profile is None


async def test_done_frame_records_lineage(db_session):
    """A completed write leaves a lineage edge behind, from the same hook that
    persists table stats and health samples."""
    from api.models.lineage import LineageEdge

    ws, catalog = await _make_workspace(db_session)
    query = Query(
        workspace_id=ws.id,
        sql=(
            f"CREATE TABLE {catalog.slug}.analytics.dim "
            f"AS SELECT * FROM {catalog.slug}.analytics.src"
        ),
        status="queued",
        active_catalog=catalog.slug,
    )
    db_session.add(query)
    await db_session.commit()

    await query_service.handle_agent_frame(
        db_session,
        Frame(type=FrameType.QUERY_DONE, payload={"query_id": str(query.id), "status": "done"}),
    )

    edges = (await db_session.execute(select(LineageEdge))).scalars().all()
    assert [(e.source_table, e.target_table) for e in edges] == [("src", "dim")]


async def test_failed_frame_records_no_lineage(db_session):
    from api.models.lineage import LineageEdge

    ws, catalog = await _make_workspace(db_session)
    query = Query(
        workspace_id=ws.id,
        sql=(
            f"CREATE TABLE {catalog.slug}.analytics.dim "
            f"AS SELECT * FROM {catalog.slug}.analytics.src"
        ),
        status="queued",
        active_catalog=catalog.slug,
    )
    db_session.add(query)
    await db_session.commit()

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": str(query.id), "status": "failed", "error": "boom"},
        ),
    )

    assert (await db_session.execute(select(LineageEdge))).scalars().all() == []


async def test_lineage_failure_does_not_break_frame_handling(db_session, monkeypatch):
    """Lineage runs on the agent's frame-receive path, so it must never be able to
    stall that path — a broken extractor costs an edge, not an agent."""
    from api.models.lineage import LineageEdge

    ws, catalog = await _make_workspace(db_session)
    # A statement that *would* produce an edge, so the assertions below can tell
    # a swallowed failure apart from a patch that never took effect.
    query = Query(
        workspace_id=ws.id,
        sql=(
            f"CREATE TABLE {catalog.slug}.analytics.dim "
            f"AS SELECT * FROM {catalog.slug}.analytics.src"
        ),
        status="queued",
        active_catalog=catalog.slug,
    )
    db_session.add(query)
    await db_session.commit()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr("api.services.lineage.ingest.record_execution_lineage", boom)

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": str(query.id), "status": "done", "row_count": 3},
        ),
    )

    await db_session.refresh(query)
    assert query.status == "done"
    assert query.row_count == 3
    assert (await db_session.execute(select(LineageEdge))).scalars().all() == []


async def test_pick_agent_for(db_session):
    ws, _catalog = await _make_workspace(db_session)
    agent = Agent(name="a", status="healthy", capabilities={"extensions": ["iceberg", "httpfs"]})
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    assert await query_service.pick_agent_for(db_session, ws) is None  # not connected

    registry.register(agent.id, object())  # type: ignore[arg-type]
    try:
        picked = await query_service.pick_agent_for(db_session, ws)
        assert picked is not None and picked.id == agent.id
    finally:
        registry.unregister(agent.id)


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        import uuid

        registry.unregister(uuid.UUID(cid))
