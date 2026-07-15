from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
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


@pytest.fixture(autouse=True)
def _spy_agent_dispatch(monkeypatch):
    """Record every CLOSE_SESSION the reaper dispatches so tests can assert the
    agent's slot is actually reclaimed, not just the DB row marked terminal."""
    calls: list[tuple] = []

    async def _spy(db, agent_id, session_id):
        calls.append((agent_id, session_id))
        return True

    monkeypatch.setattr(reaper_mod, "dispatch_close_session", _spy)
    return calls


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


async def test_reaps_idle_and_old_but_keeps_fresh(session_factory, _spy_agent_dispatch):
    ids = await _seed(session_factory)
    reaped = await run_cycle(session_factory)
    assert reaped == {"idle": 1, "max_lifetime": 1, "open_timeout": 0}

    async with session_factory() as db:
        assert (await db.get(SqlSession, ids["fresh"])).status == "open"
        assert (await db.get(SqlSession, ids["idle"])).status == "expired"
        assert (await db.get(SqlSession, ids["old"])).status == "expired"

    # Both reaped rows had their slot released via a dispatched CLOSE_SESSION.
    reaped_ids = {sid for _, sid in _spy_agent_dispatch}
    assert reaped_ids == {ids["idle"], ids["old"]}


async def test_reaps_stuck_opening_but_keeps_recent_opening(session_factory, _spy_agent_dispatch):
    async with session_factory() as db:
        u = User(email="o@reaper.local", password_hash=hash_password("pw"), name="O", role="user")
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        agent = Agent(name="ao", status="healthy", capabilities={})
        db.add(agent)
        await db.flush()
        now = datetime.now(tz=UTC)
        stuck = SqlSession(
            workspace_id=ws.id,
            agent_id=agent.id,
            status="opening",
            created_at=now - timedelta(minutes=10),
            last_active_at=now - timedelta(minutes=10),
        )
        recent = SqlSession(
            workspace_id=ws.id,
            agent_id=agent.id,
            status="opening",
            created_at=now,
            last_active_at=now,
        )
        db.add_all([stuck, recent])
        await db.commit()
        await db.refresh(stuck)
        await db.refresh(recent)
        stuck_id, recent_id = stuck.id, recent.id

    reaped = await run_cycle(session_factory)
    assert reaped == {"idle": 0, "max_lifetime": 0, "open_timeout": 1}

    async with session_factory() as db:
        reaped_row = await db.get(SqlSession, stuck_id)
        assert reaped_row.status == "failed"
        assert reaped_row.error == "open_timeout"
        assert (await db.get(SqlSession, recent_id)).status == "opening"

    # The slot a stuck open may have acquired is reclaimed via a dispatched close.
    assert {sid for _, sid in _spy_agent_dispatch} == {stuck_id}


async def test_run_tick_runs_when_leader(session_factory):
    # On SQLite leadership is always granted, so a tick runs a cycle.
    await _seed(session_factory)
    result = await run_tick(session_factory)
    assert result == {"idle": 1, "max_lifetime": 1, "open_timeout": 0}
