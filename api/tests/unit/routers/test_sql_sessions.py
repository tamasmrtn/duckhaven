import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.sql_sessions import service as session_service


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="s@sessions.local", password_hash=hash_password("pw"), name="Sess", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "s@sessions.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user: User):
    ws, _catalog = await seed_workspace(db_session, user_id=user.id)
    return ws


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(name="sess-agent", status="healthy", capabilities={"extensions": ["httpfs"]})
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest_asyncio.fixture
async def connected_agent(agent: Agent):
    registry.register(agent.id, MockWebSocket())  # type: ignore[arg-type]
    yield agent
    registry.unregister(agent.id)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "sql_sessions_enabled", True)


async def _open_session_row(db, workspace, agent, user, *, status="open") -> SqlSession:
    s = SqlSession(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=user.id,
        status=status,
        active_catalog="test_ws",
        staging_uri="/tmp/test/_staging/x/",
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def test_disabled_by_default_returns_404(authed_client, workspace):
    resp = await authed_client.post(f"/workspaces/{workspace.slug}/sql/sessions", json={})
    assert resp.status_code == 404


async def test_open_session_success(
    authed_client, workspace, connected_agent, enabled, monkeypatch
):
    async def fake_dispatch(db, session, catalogs):
        session.status = "open"
        await db.commit()
        return True

    monkeypatch.setattr(session_service, "dispatch_open_session", fake_dispatch)

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(connected_agent.id)}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "open"
    assert body["staging_uri"].endswith("/")
    assert body["agent_id"] == str(connected_agent.id)


async def test_open_session_agent_not_connected(authed_client, workspace, agent, enabled):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(agent.id)}
    )
    assert resp.status_code == 503


async def test_statement_policy_rejection(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "INSTALL httpfs"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "statement_not_allowed"


async def test_statement_on_non_open_session_conflicts(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user, status="opening")
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 409


async def test_statement_success_creates_session_query(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 202, resp.text
    query = await db_session.get(Query, uuid.UUID(resp.json()["id"]))
    assert query.origin == "session"
    assert query.session_id == session.id


async def test_close_session(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_close(db, agent_id, session_id):
        return True

    monkeypatch.setattr(session_service, "dispatch_close_session", fake_close)

    resp = await authed_client.delete(f"/sql/sessions/{session.id}")
    assert resp.status_code == 204
    await db_session.refresh(session)
    assert session.status == "closing"
