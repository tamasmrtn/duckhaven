from __future__ import annotations

import uuid

import pytest
from conftest import seed_workspace

from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.user import User
from api.services.auth import hash_password
from api.services.sql_sessions.service import (
    fail_inflight_statements,
    fail_sessions_for_agent,
    handle_session_frame,
    handle_statement_ack,
)
from duckhaven_shared.protocol import Frame, FrameType


async def _open_session(db) -> SqlSession:
    u = User(email="svc@sessions.local", password_hash=hash_password("pw"), name="S", role="user")
    db.add(u)
    await db.flush()
    ws, _ = await seed_workspace(db, user_id=u.id)
    agent = Agent(name="svc-agent", status="healthy", capabilities={})
    db.add(agent)
    await db.flush()
    s = SqlSession(workspace_id=ws.id, agent_id=agent.id, status="open")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@pytest.mark.parametrize("reason", ["agent_self_reap"])
async def test_session_closed_with_reason_closes_open_row(db_session, reason):
    session = await _open_session(db_session)
    frame = Frame(
        type=FrameType.SESSION_CLOSED,
        payload={"session_id": str(session.id), "status": "closed", "reason": reason},
    )
    await handle_session_frame(db_session, frame)

    await db_session.refresh(session)
    assert session.status == "closed"
    assert session.closed_at is not None


# ── In-flight statement resolution + ack receipt (#156) ───────────────────────


async def _statement(db, session: SqlSession, status: str = "queued") -> Query:
    q = Query(
        workspace_id=session.workspace_id,
        agent_id=session.agent_id,
        user_id=session.user_id,
        sql="SELECT 1",
        status=status,
        origin="session",
        session_id=session.id,
        timeout_s=600.0,
    )
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return q


async def test_statement_ack_marks_the_statement_running(db_session):
    """The receipt is what makes a lost frame detectable: without it `queued`
    means both "in flight to the agent" and "the agent is running it"."""
    session = await _open_session(db_session)
    q = await _statement(db_session, session)

    await handle_statement_ack(
        db_session, Frame(type=FrameType.STATEMENT_ACK, payload={"query_id": str(q.id)})
    )
    await db_session.refresh(q)
    assert q.status == "running"


async def test_statement_ack_is_idempotent(db_session):
    session = await _open_session(db_session)
    q = await _statement(db_session, session)
    frame = Frame(type=FrameType.STATEMENT_ACK, payload={"query_id": str(q.id)})

    await handle_statement_ack(db_session, frame)
    await handle_statement_ack(db_session, frame)
    await db_session.refresh(q)
    assert q.status == "running"


async def test_statement_ack_never_resurrects_a_finished_statement(db_session):
    """A fast statement's QUERY_DONE can be applied before its own ack (separate
    DB sessions), so the ack must not drag a done row back to running."""
    session = await _open_session(db_session)
    q = await _statement(db_session, session, status="done")

    await handle_statement_ack(
        db_session, Frame(type=FrameType.STATEMENT_ACK, payload={"query_id": str(q.id)})
    )
    await db_session.refresh(q)
    assert q.status == "done"


async def test_statement_ack_for_unknown_query_is_a_noop(db_session):
    await handle_statement_ack(
        db_session,
        Frame(type=FrameType.STATEMENT_ACK, payload={"query_id": str(uuid.uuid4())}),
    )


async def test_fail_inflight_statements_resolves_only_in_flight_rows(db_session):
    session = await _open_session(db_session)
    queued = await _statement(db_session, session, status="queued")
    running = await _statement(db_session, session, status="running")
    done = await _statement(db_session, session, status="done")

    count = await fail_inflight_statements(db_session, [session.id], "session closed")
    await db_session.commit()

    assert count == 2
    for q, expected in ((queued, "failed"), (running, "failed"), (done, "done")):
        await db_session.refresh(q)
        assert q.status == expected
    await db_session.refresh(queued)
    assert queued.error == "session closed"
    assert queued.finished_at is not None


async def test_agent_disconnect_resolves_in_flight_statements(db_session):
    """The #156 defect-3 regression: fail_sessions_for_agent flipped the *session*
    to failed but left its statements queued forever, so a client polling one
    polled until its own deadline against a session that no longer existed."""
    session = await _open_session(db_session)
    q = await _statement(db_session, session, status="queued")

    await fail_sessions_for_agent(db_session, session.agent_id)

    await db_session.refresh(session)
    await db_session.refresh(q)
    assert session.status == "failed"
    assert q.status == "failed"
    assert q.error == "agent disconnected"


async def test_session_closed_resolves_in_flight_statements(db_session):
    """Closing a session drops the agent's held connection; anything still queued
    on it can never run."""
    session = await _open_session(db_session)
    q = await _statement(db_session, session, status="queued")

    await handle_session_frame(
        db_session,
        Frame(
            type=FrameType.SESSION_CLOSED,
            payload={"session_id": str(session.id), "status": "closed"},
        ),
    )
    await db_session.refresh(q)
    assert q.status == "failed"
    assert q.error == "session closed"


async def test_await_session_open_waits_through_pending(db_engine):
    """The wait covers both states an open can sit in: `pending` (compute starting,
    no agent bound) and `opening` (bound, awaiting the ack). Returning at the first
    sight of a non-`opening` row would make every cold open fail immediately."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.services.sql_sessions.service import await_session_open

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        u = User(email="w@sessions.local", password_hash=hash_password("pw"), name="W", role="user")
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        agent = Agent(name="wait-agent", status="healthy", capabilities={})
        db.add(agent)
        await db.flush()
        s = SqlSession(workspace_id=ws.id, agent_id=None, status="pending")
        db.add(s)
        await db.commit()
        session_id = s.id

    async def advance():
        # Both transitions land in a *different* DB session, exactly as the binder
        # and the websocket handler do in production.
        await asyncio.sleep(0.05)
        async with factory() as other:
            row = await other.get(SqlSession, session_id)
            row.status = "opening"
            row.agent_id = agent.id
            await other.commit()
        await asyncio.sleep(0.05)
        async with factory() as other:
            row = await other.get(SqlSession, session_id)
            row.status = "open"
            await other.commit()

    async with factory() as db:
        session = await db.get(SqlSession, session_id)
        mover = asyncio.create_task(advance())
        await await_session_open(db, session, timeout_s=5.0)
        await mover

    assert session.status == "open"


async def test_await_session_open_releases_the_connection_between_polls(db_engine):
    """The poll must not hold its pooled connection idle-in-transaction for the
    whole wait. With a 45s cold-start budget and one session per dbt thread, that
    exhausts the pool and stalls every other request on the replica."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.services.sql_sessions.service import await_session_open

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        u = User(email="t@sessions.local", password_hash=hash_password("pw"), name="T", role="user")
        db.add(u)
        await db.flush()
        ws, _ = await seed_workspace(db, user_id=u.id)
        s = SqlSession(workspace_id=ws.id, agent_id=None, status="pending")
        db.add(s)
        await db.commit()
        session_id = s.id

    in_transaction: list[bool] = []

    async with factory() as db:
        session = await db.get(SqlSession, session_id)

        async def sample():
            # Sample well after the first poll has run, so a transaction opened by
            # `refresh` and never ended would still be open here.
            for _ in range(5):
                await asyncio.sleep(0.15)
                in_transaction.append(db.in_transaction())

        sampler = asyncio.create_task(sample())
        await await_session_open(db, session, timeout_s=1.0)
        await sampler

    assert not any(in_transaction), "the poll held a transaction open between polls"
