from __future__ import annotations

import pytest
from conftest import seed_workspace

from api.models.agent import Agent
from api.models.sql_session import SqlSession
from api.models.user import User
from api.services.auth import hash_password
from api.services.sql_sessions.service import handle_session_frame
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
