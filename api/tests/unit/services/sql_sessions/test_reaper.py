from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_workspace
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.query import Query
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
    assert reaped == {
        "idle": 1,
        "max_lifetime": 1,
        "open_timeout": 0,
        "statement_unacked": 0,
        "statement_timeout": 0,
    }

    async with session_factory() as db:
        assert (await db.get(SqlSession, ids["fresh"])).status == "open"
        assert (await db.get(SqlSession, ids["idle"])).status == "expired"
        assert (await db.get(SqlSession, ids["old"])).status == "expired"
        # The audit surface reads the reason off a typed column, never by parsing
        # the free-text `error`.
        assert (await db.get(SqlSession, ids["fresh"])).close_reason is None
        assert (await db.get(SqlSession, ids["idle"])).close_reason == "idle"
        assert (await db.get(SqlSession, ids["old"])).close_reason == "max_lifetime"

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
    assert reaped == {
        "idle": 0,
        "max_lifetime": 0,
        "open_timeout": 1,
        "statement_unacked": 0,
        "statement_timeout": 0,
    }

    async with session_factory() as db:
        reaped_row = await db.get(SqlSession, stuck_id)
        assert reaped_row.status == "failed"
        assert reaped_row.error == "open_timeout"
        assert reaped_row.close_reason == "open_timeout"
        assert (await db.get(SqlSession, recent_id)).status == "opening"

    # The slot a stuck open may have acquired is reclaimed via a dispatched close.
    assert {sid for _, sid in _spy_agent_dispatch} == {stuck_id}


async def test_run_tick_runs_when_leader(session_factory):
    # On SQLite leadership is always granted, so a tick runs a cycle.
    await _seed(session_factory)
    result = await run_tick(session_factory)
    assert result == {
        "idle": 1,
        "max_lifetime": 1,
        "open_timeout": 0,
        "statement_unacked": 0,
        "statement_timeout": 0,
    }


# ── Statement deadlines (#156) ────────────────────────────────────────────────
# A lost EXEC_STATEMENT frame left the row `queued` forever: the agent's timeout
# only bounds execution, so nothing bounded a statement that never started. These
# cover the two server-side deadlines that now bound it.


async def _seed_statement(
    session_factory,
    *,
    status: str,
    age_s: float,
    timeout_s: float | None = 600.0,
    origin: str = "session",
    features: list[str] | None = None,
) -> uuid.UUID:
    """One session statement of a given status/age on an agent that does (or does
    not) advertise statement acks."""
    async with session_factory() as db:
        u = User(
            email=f"s{uuid.uuid4().hex[:8]}@reaper.local",
            password_hash=hash_password("pw"),
            name="S",
            role="user",
        )
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        feature_list = features if features is not None else ["statement_ack"]
        agent = Agent(
            name=f"a-{uuid.uuid4().hex[:6]}",
            status="healthy",
            capabilities={"protocol_features": feature_list},
        )
        db.add(agent)
        await db.flush()
        sess = SqlSession(
            workspace_id=ws.id,
            agent_id=agent.id,
            status="open",
            created_at=datetime.now(tz=UTC),
            last_active_at=datetime.now(tz=UTC),
        )
        db.add(sess)
        await db.flush()
        q = Query(
            workspace_id=ws.id,
            agent_id=agent.id,
            user_id=u.id,
            sql="SELECT 1",
            status=status,
            origin=origin,
            session_id=sess.id,
            timeout_s=timeout_s,
            started_at=datetime.now(tz=UTC) - timedelta(seconds=age_s),
        )
        db.add(q)
        await db.commit()
        await db.refresh(q)
        return q.id


async def _status(session_factory, query_id) -> tuple[str, str | None]:
    async with session_factory() as db:
        q = await db.get(Query, query_id)
        return q.status, q.error


async def test_reaps_queued_statement_the_agent_never_acked(session_factory):
    """The #156 regression: the agent never acked, so the frame never landed.
    Previously this row stayed queued forever and the client hung ~10.5 minutes."""
    qid = await _seed_statement(session_factory, status="queued", age_s=60)
    reaped = await run_cycle(session_factory)

    assert reaped["statement_unacked"] == 1
    assert await _status(session_factory, qid) == ("failed", "agent did not ack statement")


async def test_keeps_queued_statement_within_the_ack_deadline(session_factory):
    """A statement submitted a moment ago has not missed its ack yet."""
    qid = await _seed_statement(session_factory, status="queued", age_s=1)
    reaped = await run_cycle(session_factory)

    assert reaped["statement_unacked"] == 0
    assert (await _status(session_factory, qid))[0] == "queued"


async def test_keeps_running_statement_within_its_timeout(session_factory):
    """An acked, long-running statement is untouched until its own budget plus
    grace elapses — the agent is still working on it."""
    qid = await _seed_statement(session_factory, status="running", age_s=120, timeout_s=600.0)
    reaped = await run_cycle(session_factory)

    assert reaped["statement_timeout"] == 0
    assert (await _status(session_factory, qid))[0] == "running"


async def test_reaps_running_statement_past_its_timeout_and_grace(session_factory):
    """Past timeout_s + grace the agent should have reported its own timeout, so
    its reply is gone too."""
    qid = await _seed_statement(session_factory, status="running", age_s=700, timeout_s=600.0)
    reaped = await run_cycle(session_factory)

    assert reaped["statement_timeout"] == 1
    assert await _status(session_factory, qid) == ("failed", "statement exceeded timeout")


async def test_running_statement_respects_a_short_custom_timeout(session_factory):
    """The deadline follows the statement's own persisted budget, not a fixed one."""
    qid = await _seed_statement(session_factory, status="running", age_s=90, timeout_s=30.0)
    reaped = await run_cycle(session_factory)

    assert reaped["statement_timeout"] == 1
    assert (await _status(session_factory, qid))[0] == "failed"


async def test_null_timeout_falls_back_to_the_default(session_factory):
    """Rows written before the timeout_s column existed still get a bound."""
    qid = await _seed_statement(session_factory, status="running", age_s=700, timeout_s=None)
    reaped = await run_cycle(session_factory)

    assert reaped["statement_timeout"] == 1
    assert (await _status(session_factory, qid))[0] == "failed"


async def test_never_reaps_non_session_queries(session_factory):
    """Interactive queries are dispatched differently and are out of scope; the
    session reaper must not touch them."""
    qid = await _seed_statement(session_factory, status="queued", age_s=3600, origin=None)
    reaped = await run_cycle(session_factory)

    assert reaped["statement_unacked"] == 0
    assert reaped["statement_timeout"] == 0
    assert (await _status(session_factory, qid))[0] == "queued"


async def test_agent_without_ack_support_is_not_reaped_at_the_ack_deadline(session_factory):
    """Rollout safety: an older agent never sends STATEMENT_ACK. Applying the
    short ack deadline to it would fail *every* statement seconds after submit
    whenever the API is upgraded ahead of its agents."""
    qid = await _seed_statement(session_factory, status="queued", age_s=60, features=[])
    reaped = await run_cycle(session_factory)

    assert reaped["statement_unacked"] == 0
    assert (await _status(session_factory, qid))[0] == "queued"


async def test_agent_without_ack_support_still_bounded_by_its_timeout(session_factory):
    """...but such a statement is still bounded — just by the slower deadline,
    never forever."""
    qid = await _seed_statement(session_factory, status="queued", age_s=700, features=[])
    reaped = await run_cycle(session_factory)

    assert reaped["statement_timeout"] == 1
    assert (await _status(session_factory, qid))[0] == "failed"


async def test_expiring_a_session_resolves_its_in_flight_statements(session_factory):
    """Reaping a session drops the agent's held connection, so a statement queued
    on it can never run. Previously these were orphaned — the reporter had rows
    queued for 4.4 hours whose session was long expired."""
    async with session_factory() as db:
        u = User(
            email="orph@reaper.local", password_hash=hash_password("pw"), name="O", role="user"
        )
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        agent = Agent(name="orph-a", status="healthy", capabilities={})
        db.add(agent)
        await db.flush()
        sess = SqlSession(
            workspace_id=ws.id,
            agent_id=agent.id,
            status="open",
            created_at=datetime.now(tz=UTC),
            last_active_at=datetime.now(tz=UTC) - timedelta(hours=1),  # idle -> reaped
        )
        db.add(sess)
        await db.flush()
        q = Query(
            workspace_id=ws.id,
            agent_id=agent.id,
            user_id=u.id,
            sql="SELECT 1",
            status="running",
            origin="session",
            session_id=sess.id,
            timeout_s=600.0,
        )
        db.add(q)
        await db.commit()
        await db.refresh(q)
        qid = sid = q.id
        sid = sess.id

    await run_cycle(session_factory)

    async with session_factory() as db:
        assert (await db.get(SqlSession, sid)).status == "expired"
    assert await _status(session_factory, qid) == ("failed", "session expired")


async def test_opening_deadline_is_measured_from_opening_at(session_factory, _spy_agent_dispatch):
    """A session that waited out a cold start gets its full opening budget.

    The deadline used to be measured from `created_at`, which is when the *client*
    asked -- so a session that sat pending for most of a slow provision arrived at
    `opening` already past it and was reaped on the very next tick, before the
    agent had any chance to ack.
    """
    async with session_factory() as db:
        u = User(email="c@reaper.local", password_hash=hash_password("pw"), name="C", role="user")
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        agent = Agent(name="ac", status="healthy", capabilities={})
        db.add(agent)
        await db.flush()
        now = datetime.now(tz=UTC)
        cold = SqlSession(
            workspace_id=ws.id,
            agent_id=agent.id,
            status="opening",
            created_at=now - timedelta(minutes=10),
            opening_at=now,
            last_active_at=now,
        )
        db.add(cold)
        await db.commit()
        await db.refresh(cold)
        cold_id = cold.id

    reaped = await run_cycle(session_factory)
    assert reaped["open_timeout"] == 0

    async with session_factory() as db:
        assert (await db.get(SqlSession, cold_id)).status == "opening"


async def test_pending_sessions_are_left_to_the_compute_reaper(
    session_factory, _spy_agent_dispatch
):
    """A pending session has no agent to be stuck on. Bounding it here would use
    the wrong budget entirely -- the compute reaper's provisioning deadline is what
    says compute is never coming."""
    async with session_factory() as db:
        u = User(email="p@reaper.local", password_hash=hash_password("pw"), name="P", role="user")
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        now = datetime.now(tz=UTC)
        pending = SqlSession(
            workspace_id=ws.id,
            agent_id=None,
            status="pending",
            created_at=now - timedelta(minutes=10),
            last_active_at=now - timedelta(minutes=10),
        )
        db.add(pending)
        await db.commit()
        await db.refresh(pending)
        pending_id = pending.id

    reaped = await run_cycle(session_factory)
    assert reaped["open_timeout"] == 0
    assert reaped["idle"] == 0

    async with session_factory() as db:
        assert (await db.get(SqlSession, pending_id)).status == "pending"
