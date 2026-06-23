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


async def _make_workspace(db_session):
    user = User(email="svc@test.local", password_hash=hash_password("pw"), name="Svc", role="user")
    db_session.add(user)
    await db_session.flush()
    return await seed_workspace(db_session, user_id=user.id, slug="svc-ws", name="Svc WS")


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
