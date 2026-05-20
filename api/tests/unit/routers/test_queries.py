import uuid

import pytest_asyncio
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.agent_registry import registry
from api.services.auth import hash_password


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(
        email="q@queries.local",
        password_hash=hash_password("pw"),
        name="Querier",
        role="user",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "q@queries.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user: User):
    backend = StorageBackend(
        kind="local_fs",
        name="test-store",
        root_uri="/tmp/test",
        created_by=user.id,
    )
    db_session.add(backend)
    await db_session.flush()

    ws = Workspace(slug="test-ws", name="Test WS", storage_backend_id=backend.id)
    db_session.add(ws)
    await db_session.flush()

    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(ws)
    return ws


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(name="test-agent", status="healthy")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest_asyncio.fixture
async def connected_agent(agent: Agent):
    mock_ws = MockWebSocket()
    registry.register(agent.id, mock_ws)  # type: ignore[arg-type]
    yield agent, mock_ws
    registry.unregister(agent.id)


# --- query dispatch ---


async def test_create_query_agent_not_connected(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent
):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 503


async def test_create_query_agent_not_found(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_create_query_dispatches(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 42", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "running"
    assert data["sql"] == "SELECT 42"
    assert len(mock_ws.sent) == 1
    import json

    from duckhaven_shared.protocol import FrameType

    frame = json.loads(mock_ws.sent[0])
    assert frame["type"] == FrameType.DISPATCH_QUERY
    assert frame["payload"]["sql"] == "SELECT 42"


# --- query status ---


async def test_get_query(
    authed_client: AsyncClient, workspace: Workspace, connected_agent, db_session
):
    agent, _ = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = resp.json()["id"]

    resp = await authed_client.get(f"/queries/{query_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == query_id


async def test_get_query_not_found(authed_client: AsyncClient):
    resp = await authed_client.get(f"/queries/{uuid.uuid4()}")
    assert resp.status_code == 404


# --- cancel ---


async def test_cancel_query(authed_client: AsyncClient, workspace: Workspace, connected_agent):
    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = resp.json()["id"]

    resp = await authed_client.delete(f"/queries/{query_id}")
    assert resp.status_code == 204

    resp = await authed_client.get(f"/queries/{query_id}")
    assert resp.json()["status"] == "cancelled"


# --- rows ---


async def test_rows_query_not_done(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, _ = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = resp.json()["id"]

    resp = await authed_client.get(f"/queries/{query_id}/rows")
    assert resp.status_code == 409


# --- saved queries ---


async def test_list_saved_queries_empty(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/saved-queries")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_saved_query(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "My Query", "sql": "SELECT 1"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Query"
    assert data["sql"] == "SELECT 1"


async def test_get_query_rows_agent_no_result_host(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    from datetime import UTC, datetime

    from api.models.query import Query

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="done",
        result_path="/tmp/fake.parquet",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 503


async def test_cancel_query_agent_not_connected(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    from datetime import UTC, datetime

    from api.models.query import Query

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="running",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    resp = await authed_client.delete(f"/queries/{query.id}")
    assert resp.status_code == 204

    resp = await authed_client.get(f"/queries/{query.id}")
    assert resp.json()["status"] == "cancelled"


async def test_create_saved_query_reader_role_rejected(
    authed_client: AsyncClient, workspace: Workspace, db_session
):
    from api.models.user import User
    from api.models.workspace import WorkspaceMember
    from api.services.auth import hash_password

    reader = User(
        email="reader@queries.local",
        password_hash=hash_password("pw"),
        name="Reader",
        role="user",
    )
    db_session.add(reader)
    await db_session.flush()
    member = WorkspaceMember(workspace_id=workspace.id, user_id=reader.id, role="reader")
    db_session.add(member)
    await db_session.commit()

    await authed_client.post("/auth/logout")
    await authed_client.post(
        "/auth/login", json={"email": "reader@queries.local", "password": "pw"}
    )

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Test", "sql": "SELECT 1"},
    )
    assert resp.status_code == 403
