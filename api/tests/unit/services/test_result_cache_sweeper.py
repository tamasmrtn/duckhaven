"""Result cache expiry (`services/result_cache/sweeper.py`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.query import Query
from api.models.result_cache import ResultCacheEntry
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.result_cache.sweeper import run_cycle


class _WS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


@pytest.fixture
def sessions(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def test_expired_entries_go_and_their_agent_files_are_released(sessions) -> None:
    now = datetime.now(tz=UTC)
    async with sessions() as db:
        user = User(email="w@s.local", password_hash=hash_password("pw"), name="W")
        db.add(user)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=user.id)
        agent = Agent(name="a", status="healthy", capabilities={})
        db.add(agent)
        await db.flush()

        async def entry(*, storage: str, expires: datetime, hard: datetime) -> uuid.UUID:
            source = Query(workspace_id=ws.id, sql="SELECT 1", status="done")
            db.add(source)
            await db.flush()
            e = ResultCacheEntry(
                workspace_id=ws.id,
                key_hash=uuid.uuid4().hex * 2,
                sql="SELECT 1",
                context={},
                tables=[],
                source_query_id=source.id,
                storage=storage,
                agent_id=agent.id if storage == "agent" else None,
                inline_parquet=b"x" if storage == "inline" else None,
                result_bytes=10,
                row_count=1,
                expires_at=expires,
                hard_expires_at=hard,
            )
            db.add(e)
            await db.flush()
            return e.id

        live = await entry(
            storage="inline", expires=now + timedelta(hours=1), hard=now + timedelta(days=1)
        )
        idle = await entry(
            storage="agent", expires=now - timedelta(minutes=1), hard=now + timedelta(days=1)
        )
        old = await entry(
            storage="inline", expires=now + timedelta(hours=1), hard=now - timedelta(minutes=1)
        )
        await db.commit()
        agent_id = agent.id

    socket = _WS()
    registry.register(agent_id, socket)  # type: ignore[arg-type]
    try:
        result = await run_cycle(sessions, now=now)
    finally:
        registry.unregister(agent_id)

    assert result["expired"] == 2
    async with sessions() as db:
        assert set((await db.scalars(select(ResultCacheEntry.id))).all()) == {live}
    assert idle and old
    assert len(socket.sent) == 1 and "release_result" in socket.sent[0]
