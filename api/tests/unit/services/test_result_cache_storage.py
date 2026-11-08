"""Result cache storage: inline space budgets and scheduled runs answered from the
cache (`services/result_cache/storage.py`, scheduler integration)."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import duckdb
import httpx
import pytest
from conftest import seed_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query, SavedQuery, Schedule
from api.models.result_cache import ResultCacheEntry
from api.models.user import User
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.result_cache import service as result_cache
from api.services.result_cache.storage import enforce_inline_quota
from api.services.scheduler.scanner import run_cycle
from duckhaven_shared.protocol import Frame, FrameType


@pytest.fixture
def sessions(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _workspace(db) -> uuid.UUID:
    user = User(
        email=f"{uuid.uuid4().hex[:6]}@s.local", password_hash=hash_password("pw"), name="S"
    )
    db.add(user)
    await db.flush()
    ws, _ = await seed_workspace(db, user_id=user.id, slug=f"ws-{uuid.uuid4().hex[:6]}")
    return ws.id


async def _entry(
    db, workspace_id, *, size: int, compute_ms: int, hits: int, age_s: float
) -> uuid.UUID:
    source = Query(workspace_id=workspace_id, sql="SELECT 1", status="done")
    db.add(source)
    await db.flush()
    now = datetime.now(tz=UTC)
    entry = ResultCacheEntry(
        workspace_id=workspace_id,
        key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        sql="SELECT 1",
        context={},
        tables=[],
        source_query_id=source.id,
        storage="inline",
        inline_parquet=b"x",
        result_bytes=size,
        row_count=1,
        compute_ms=compute_ms,
        hit_count=hits,
        created_at=now - timedelta(seconds=age_s),
        expires_at=now + timedelta(hours=1),
        hard_expires_at=now + timedelta(hours=2),
    )
    db.add(entry)
    await db.commit()
    return entry.id


async def _ids(db) -> set[uuid.UUID]:
    return set((await db.scalars(select(ResultCacheEntry.id))).all())


async def test_the_least_profitable_entries_go_first(sessions, monkeypatch) -> None:
    monkeypatch.setattr(settings, "result_cache_inline_workspace_max_bytes", 250)
    monkeypatch.setattr(settings, "result_cache_min_lease_s", 0)
    async with sessions() as db:
        ws = await _workspace(db)
        cheap = await _entry(db, ws, size=100, compute_ms=10, hits=0, age_s=3600)
        expensive = await _entry(db, ws, size=100, compute_ms=60_000, hits=0, age_s=3600)
        popular = await _entry(db, ws, size=100, compute_ms=10, hits=500, age_s=3600)
        assert await enforce_inline_quota(db, ws) == 1
        assert await _ids(db) == {expensive, popular}
        assert cheap not in await _ids(db)


async def test_young_entries_are_leased(sessions, monkeypatch) -> None:
    monkeypatch.setattr(settings, "result_cache_inline_workspace_max_bytes", 100)
    monkeypatch.setattr(settings, "result_cache_min_lease_s", 600)
    async with sessions() as db:
        ws = await _workspace(db)
        old = await _entry(db, ws, size=100, compute_ms=60_000, hits=9, age_s=3600)
        young = await _entry(db, ws, size=100, compute_ms=1, hits=0, age_s=5)
        await enforce_inline_quota(db, ws)
        # The young entry is worth less but cannot be evicted yet.
        assert await _ids(db) == {young}
        assert old not in await _ids(db)


async def test_the_total_budget_spans_workspaces(sessions, monkeypatch) -> None:
    monkeypatch.setattr(settings, "result_cache_inline_total_max_bytes", 150)
    monkeypatch.setattr(settings, "result_cache_min_lease_s", 0)
    async with sessions() as db:
        a = await _workspace(db)
        b = await _workspace(db)
        await _entry(db, a, size=100, compute_ms=10, hits=0, age_s=3600)
        keep = await _entry(db, b, size=100, compute_ms=90_000, hits=3, age_s=3600)
        await enforce_inline_quota(db, a)
        assert await _ids(db) == {keep}


# --- Scheduled runs ----------------------------------------------------------


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


async def _done_on_agent_at(db, host: str) -> Query:
    workspace_id = await _workspace(db)
    agent = Agent(name="a", status="healthy", result_host=host, result_port=8001)
    db.add(agent)
    await db.flush()
    query = Query(
        workspace_id=workspace_id, agent_id=agent.id, sql="SELECT 1", status="done", result_bytes=1
    )
    db.add(query)
    await db.commit()
    return query


async def test_a_stored_host_that_is_not_bare_is_never_fetched(sessions, monkeypatch) -> None:
    """Cache admission and revalidation read from the agent only at a bare host."""
    from api.services import agent_dispatch
    from api.services.result_cache.storage import store

    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport))

    async def connected(db, agent_id) -> bool:
        return True

    monkeypatch.setattr(agent_dispatch, "is_agent_connected", connected)

    async with sessions() as db:
        query = await _done_on_agent_at(db, "agent.example/path")
        assert await store(db, query) is None
        entry = ResultCacheEntry(agent_id=query.agent_id, source_query_id=query.id)
        assert await result_cache._agent_result_available(db, entry) is False
    assert requests == []


def _parquet() -> bytes:
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        conn = duckdb.connect()
        conn.execute(f"COPY (SELECT 42 AS answer) TO '{path}' (FORMAT PARQUET)")
        conn.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


async def test_an_unchanged_scheduled_run_is_answered_from_the_cache(
    sessions, monkeypatch, fake_polaris
) -> None:
    monkeypatch.setattr(settings, "result_cache_enabled", True)
    monkeypatch.setattr(result_cache, "admission_session_factory", sessions)
    body = _parquet()

    async def fake_proxy_rows(agent, query, *, row_offset=None, row_limit=None, token=None):
        return httpx.Response(200, content=body)

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy_rows)
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    async with sessions() as db:
        ws = await _workspace(db)
        creator = (await db.scalars(select(User))).first()
        agent = Agent(
            name="a",
            status="healthy",
            capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"], "timezone": "UTC"},
            result_host="agent.local",
            result_port=8001,
        )
        db.add(agent)
        await db.flush()
        saved = SavedQuery(
            workspace_id=ws, name="n", sql="SELECT 42 AS answer", created_by=creator.id
        )
        db.add(saved)
        await db.flush()
        schedule = Schedule(
            workspace_id=ws,
            saved_query_id=saved.id,
            cron="0 2 * * *",
            next_run_at=now - timedelta(minutes=1),
            created_by=creator.id,
        )
        db.add(schedule)
        await db.commit()
        agent_id, schedule_id = agent.id, schedule.id
    socket = FakeWS()
    registry.register(agent_id, socket)  # type: ignore[arg-type]
    try:
        await run_cycle(sessions, now=now, polaris=fake_polaris)
        async with sessions() as db:
            first = await db.scalar(select(Query).where(Query.schedule_id == schedule_id))
        assert first.cache_status == "miss"
        frame = Frame(
            type=FrameType.QUERY_DONE,
            payload={
                "query_id": str(first.id),
                "status": "done",
                "row_count": 1,
                "duration_ms": 900,
                "result_bytes": len(body),
                "result_path": "/r/x.parquet",
            },
        )
        async with sessions() as db:
            await query_service.handle_agent_frame(db, frame, fake_polaris)
        await result_cache.drain_admissions()

        async with sessions() as db:
            (await db.get(Schedule, schedule_id)).next_run_at = now - timedelta(minutes=1)
            await db.commit()
        await run_cycle(sessions, now=now, polaris=fake_polaris)
        async with sessions() as db:
            runs = (
                await db.scalars(
                    select(Query).where(Query.schedule_id == schedule_id).order_by(Query.started_at)
                )
            ).all()
        second = next(r for r in runs if r.id != first.id)
        assert (second.status, second.cache_status) == ("done", "hit")
        assert second.result_source_query_id == first.id
        assert second.origin == "scheduled"
        dispatched = [f for f in socket.sent if f["type"] == FrameType.DISPATCH_QUERY]
        assert len(dispatched) == 1
    finally:
        registry.unregister(agent_id)
