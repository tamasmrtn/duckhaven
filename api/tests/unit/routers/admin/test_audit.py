from datetime import datetime

import pytest
from api.models.agent import Agent
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace
from api.services.auth import hash_password
from httpx import AsyncClient


@pytest.fixture
async def admin(db_session):
    u = User(
        email="admin@audit.local",
        password_hash=hash_password("pw"),
        name="Admin",
        role="admin",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def regular_user(db_session):
    u = User(
        email="user@audit.local",
        password_hash=hash_password("pw"),
        name="User",
        role="user",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User):
    await client.post("/auth/login", json={"email": "admin@audit.local", "password": "pw"})
    return client


@pytest.fixture
async def user_client(client: AsyncClient, regular_user: User):
    await client.post("/auth/login", json={"email": "user@audit.local", "password": "pw"})
    return client


@pytest.fixture
async def workspace(db_session, admin: User) -> Workspace:
    sb = StorageBackend(
        kind="local_fs", name="audit-store", root_uri="/tmp/audit", created_by=admin.id
    )
    db_session.add(sb)
    await db_session.flush()
    ws = Workspace(slug="audit-ws", name="Audit WS", storage_backend_id=sb.id)
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    return ws


async def _make_query(db_session, workspace: Workspace, *, agent_id=None, started_at=None) -> Query:
    kwargs: dict = {"workspace_id": workspace.id, "sql": "SELECT 1", "status": "done"}
    if agent_id is not None:
        kwargs["agent_id"] = agent_id
    if started_at is not None:
        kwargs["started_at"] = started_at
    q = Query(**kwargs)
    db_session.add(q)
    await db_session.commit()
    await db_session.refresh(q)
    return q


async def test_audit_empty_returns_empty_list(admin_client: AsyncClient):
    resp = await admin_client.get("/admin/audit")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_audit_returns_all_queries(
    admin_client: AsyncClient, workspace: Workspace, db_session
):
    await _make_query(db_session, workspace)
    await _make_query(db_session, workspace)
    resp = await admin_client.get("/admin/audit")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_audit_filter_by_workspace_id(
    admin_client: AsyncClient, workspace: Workspace, db_session, admin: User
):
    sb2 = StorageBackend(
        kind="local_fs", name="other-store", root_uri="/tmp/other", created_by=admin.id
    )
    db_session.add(sb2)
    await db_session.flush()
    ws2 = Workspace(slug="other-ws", name="Other", storage_backend_id=sb2.id)
    db_session.add(ws2)
    await db_session.flush()

    await _make_query(db_session, workspace)
    await _make_query(db_session, ws2)

    resp = await admin_client.get(f"/admin/audit?workspace_id={workspace.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["workspace_id"] == str(workspace.id)


async def test_audit_filter_by_agent_id(
    admin_client: AsyncClient, workspace: Workspace, db_session
):
    a1 = Agent(name="agent-1", status="healthy")
    a2 = Agent(name="agent-2", status="healthy")
    db_session.add_all([a1, a2])
    await db_session.flush()

    await _make_query(db_session, workspace, agent_id=a1.id)
    await _make_query(db_session, workspace, agent_id=a2.id)

    resp = await admin_client.get(f"/admin/audit?agent_id={a1.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["agent_id"] == str(a1.id)


async def test_audit_filter_by_since(admin_client: AsyncClient, workspace: Workspace, db_session):
    await _make_query(db_session, workspace, started_at=datetime(2026, 1, 1))
    await _make_query(db_session, workspace, started_at=datetime(2026, 5, 1))

    resp = await admin_client.get("/admin/audit?since=2026-03-01T00:00:00")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_audit_filter_by_until(admin_client: AsyncClient, workspace: Workspace, db_session):
    await _make_query(db_session, workspace, started_at=datetime(2026, 1, 1))
    await _make_query(db_session, workspace, started_at=datetime(2026, 5, 1))

    resp = await admin_client.get("/admin/audit?until=2026-03-01T00:00:00")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_audit_limit_param(admin_client: AsyncClient, workspace: Workspace, db_session):
    for _ in range(5):
        await _make_query(db_session, workspace)

    resp = await admin_client.get("/admin/audit?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


async def test_audit_non_admin_forbidden(user_client: AsyncClient):
    resp = await user_client.get("/admin/audit")
    assert resp.status_code == 403
