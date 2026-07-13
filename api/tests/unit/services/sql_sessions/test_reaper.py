from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from conftest import seed_workspace
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.sql_session import SqlSession
from api.models.user import User
from api.services.auth import hash_password
from api.services.sql_sessions import reaper as reaper_mod
from api.services.sql_sessions.reaper import run_cycle, run_tick


@pytest.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _no_agent_dispatch(monkeypatch):
    async def _noop(db, agent_id, session_id):
        return True

    monkeypatch.setattr(reaper_mod, "dispatch_close_session", _noop)


async def _seed(session_factory) -> dict[str, SqlSession]:
    async with session_factory() as db:
        u = User(email="r@reaper.local", password_hash=hash_password("pw"), name="R", role="user")
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        agent = Agent(name="a", status="healthy", capabilities={})
        db.add(agent)
        await db.flush()

        now = datetime.now(tz=UTC)
        rows = {
            "fresh": SqlSession(
                workspace_id=ws.id,
                agent_id=agent.id,
                status="open",
                created_at=now,
                last_active_at=now,
            ),
            "idle": SqlSession(
                workspace_id=ws.id,
                agent_id=agent.id,
                status="open",
                created_at=now,
                last_active_at=now - timedelta(hours=1),
            ),
            "old": SqlSession(
                workspace_id=ws.id,
                agent_id=agent.id,
                status="open",
                created_at=now - timedelta(hours=10),
                last_active_at=now,
            ),
        }
        db.add_all(rows.values())
        await db.commit()
        for r in rows.values():
            await db.refresh(r)
        return {k: v.id for k, v in rows.items()}


async def test_reaps_idle_and_old_but_keeps_fresh(session_factory):
    ids = await _seed(session_factory)
    reaped = await run_cycle(session_factory)
    assert reaped == {"idle": 1, "max_lifetime": 1}

    async with session_factory() as db:
        assert (await db.get(SqlSession, ids["fresh"])).status == "open"
        assert (await db.get(SqlSession, ids["idle"])).status == "expired"
        assert (await db.get(SqlSession, ids["old"])).status == "expired"


async def test_run_tick_runs_when_leader(session_factory):
    # On SQLite leadership is always granted, so a tick runs a cycle.
    await _seed(session_factory)
    result = await run_tick(session_factory)
    assert result == {"idle": 1, "max_lifetime": 1}
