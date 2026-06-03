import json
import uuid
from datetime import UTC

import pytest
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
async def admin(db_session):
    u = User(
        email="admin@agents.local", password_hash=hash_password("pw"), name="Admin", role="admin"
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User):
    await client.post("/auth/login", json={"email": "admin@agents.local", "password": "pw"})
    return client


async def test_ws_bad_token_does_not_create_agent(ws_client, db_engine):
    """A bad bootstrap token must not result in an Agent row being created."""
    import asyncio

    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with AsyncClient(transport=ws_client, base_url="http://test") as c:
        async with aconnect_ws("http://test/agents/connect", c) as ws:
            await ws.send_text(json.dumps({"type": "auth", "payload": {"token": "bad-token"}}))
            await asyncio.sleep(0.1)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        result = await db.execute(select(func.count()).select_from(Agent))
        assert result.scalar_one() == 0


async def test_list_agents_for_picker_marks_disconnected(admin_client: AsyncClient, db_session):
    agent = Agent(name="offline-agent", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    resp = await admin_client.get("/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "offline-agent"
    assert data[0]["status"] == "unavailable"


async def test_list_agents_with_capabilities(admin_client: AsyncClient, db_session):
    agent = Agent(
        name="capable-agent",
        status="healthy",
        capabilities={
            "duckdb_version": "1.5.2",
            "extensions": ["iceberg", "httpfs"],
            "memory_limit_gb": 6.0,
            "cores": 4,
            "host": "testbox",
        },
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    class _FakeWS:
        async def send_text(self, text: str) -> None:
            pass

    from api.services.agent_registry import registry

    registry.register(agent.id, _FakeWS())  # type: ignore[arg-type]
    try:
        resp = await admin_client.get("/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "healthy"
        assert data[0]["capabilities"]["duckdb_version"] == "1.5.2"
    finally:
        registry.unregister(agent.id)


async def test_ws_valid_bootstrap_exchange(ws_client, db_engine):
    import asyncio
    from datetime import datetime, timedelta

    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.user import Credential

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    token = "dh_boot_wstest456"

    async with factory() as db:
        cred = Credential(
            user_id=None,
            agent_id=None,
            kind="agent_bootstrap",
            token=token,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
        db.add(cred)
        await db.commit()

    received: dict = {}
    async with AsyncClient(transport=ws_client, base_url="http://test") as c:
        async with aconnect_ws("http://test/agents/connect", c) as ws:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "auth",
                        "payload": {
                            "token": token,
                            "name": "ws-test-agent",
                            "result_port": 8001,
                        },
                    }
                )
            )
            raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
            received.update(json.loads(raw))

    assert received.get("type") == "auth_ok"
    assert "agent_id" in received.get("payload", {})
    assert "session_token" in received.get("payload", {})

    # The advertised result port and the socket peer host are persisted so the
    # control plane can later fetch result Parquet from the agent.
    agent_id = uuid.UUID(received["payload"]["agent_id"])
    async with factory() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        assert agent.result_port == 8001
        assert agent.result_host
