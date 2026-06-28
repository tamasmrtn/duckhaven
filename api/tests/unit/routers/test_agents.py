import json
import uuid
from datetime import UTC
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.user import User
from api.routers.agents_ws import _result_host
from api.services.auth import hash_password


def _fake_ws(*, xff: str | None, peer: str | None):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(headers=headers, client=client)


def test_result_host_prefers_forwarded_for():
    """Behind a proxy the agent's real address is the left-most X-Forwarded-For
    hop, not the socket peer (which is the load balancer)."""
    ws = _fake_ws(xff="10.0.0.5, 172.30.0.10", peer="172.30.0.10")
    assert _result_host(ws) == "10.0.0.5"


def test_result_host_falls_back_to_peer_without_forwarded_for():
    ws = _fake_ws(xff=None, peer="172.30.0.7")
    assert _result_host(ws) == "172.30.0.7"


def test_result_host_ignores_blank_forwarded_for():
    ws = _fake_ws(xff="   ", peer="172.30.0.7")
    assert _result_host(ws) == "172.30.0.7"


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


async def test_list_agents_exposes_cpu_capability_fields(admin_client: AsyncClient, db_session):
    """The new CPU capability fields round-trip through AgentCapabilitiesOut."""
    agent = Agent(
        name="cpu-agent",
        status="unavailable",
        capabilities={
            "duckdb_version": "1.5.2",
            "extensions": [],
            "memory_limit_gb": 6.0,
            "cores": 8,
            "cpu_model": "AMD EPYC 7763",
            "cpu_cores_physical": 4,
        },
    )
    db_session.add(agent)
    await db_session.commit()

    resp = await admin_client.get("/agents")
    assert resp.status_code == 200
    caps = resp.json()[0]["capabilities"]
    assert caps["cores"] == 8
    assert caps["cpu_model"] == "AMD EPYC 7763"
    assert caps["cpu_cores_physical"] == 4


async def test_ws_metrics_sample_buffered_not_persisted(ws_client, db_engine):
    """A METRICS_SAMPLE frame lands in the in-memory ring buffer and never the DB."""
    import asyncio
    from datetime import datetime, timedelta

    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.user import Credential
    from api.services.agent_registry import registry

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    token = "dh_boot_metrics789"
    async with factory() as db:
        db.add(
            Credential(
                user_id=None,
                agent_id=None,
                kind="agent_bootstrap",
                token=token,
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        )
        await db.commit()

    sample = {"cpu_percent": 77.0, "memory_percent": 12.0, "sampled_at": "2026-06-05T00:00:00Z"}
    async with AsyncClient(transport=ws_client, base_url="http://test") as c:
        async with aconnect_ws("http://test/agents/connect", c) as ws:
            await ws.send_text(
                json.dumps(
                    {"type": "auth", "payload": {"token": token, "name": "m", "result_port": 8001}}
                )
            )
            raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
            agent_id = uuid.UUID(json.loads(raw)["payload"]["agent_id"])

            await ws.send_text(json.dumps({"type": "metrics_sample", "payload": sample}))
            await asyncio.sleep(0.1)
            # Buffered while connected (lost on disconnect, so assert in-context).
            assert registry.recent_metrics().get(str(agent_id)) == [sample]

    async with factory() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        assert agent.capabilities is None  # metrics never touch the capabilities column


async def test_ws_metrics_sample_refreshes_presence(ws_client, db_engine, monkeypatch):
    """A METRICS_SAMPLE advances last_ping_at so peer replicas keep seeing the
    agent connected. Regression: the agent never sends proactive HEARTBEATs (it
    only echoes server ones), so without refreshing presence on the metrics frame
    last_ping_at went stale after the TTL and cross-replica dispatch broke."""
    import asyncio
    from datetime import datetime, timedelta

    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.user import Credential
    from api.routers import agents_ws

    # Refresh on every metrics frame instead of throttling, so the test is fast.
    monkeypatch.setattr(agents_ws, "_PRESENCE_REFRESH_S", 0.0)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    token = "dh_boot_presence321"
    async with factory() as db:
        db.add(
            Credential(
                user_id=None,
                agent_id=None,
                kind="agent_bootstrap",
                token=token,
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        )
        await db.commit()

    sample = {"cpu_percent": 1.0, "memory_percent": 2.0, "sampled_at": "2026-06-05T00:00:00Z"}
    async with AsyncClient(transport=ws_client, base_url="http://test") as c:
        async with aconnect_ws("http://test/agents/connect", c) as ws:
            await ws.send_text(
                json.dumps(
                    {"type": "auth", "payload": {"token": token, "name": "p", "result_port": 8001}}
                )
            )
            raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
            agent_id = uuid.UUID(json.loads(raw)["payload"]["agent_id"])

            async with factory() as db:
                before = (await db.get(Agent, agent_id)).last_ping_at
            await asyncio.sleep(0.01)
            await ws.send_text(json.dumps({"type": "metrics_sample", "payload": sample}))
            await asyncio.sleep(0.1)
            async with factory() as db:
                after = (await db.get(Agent, agent_id)).last_ping_at

    assert before is not None and after is not None
    assert after > before


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


async def _connect_once(ws_client, payload: dict) -> dict:
    """Open the agent WS, send one auth frame, return the first server frame."""
    import asyncio

    from httpx import AsyncClient
    from httpx_ws import aconnect_ws

    received: dict = {}
    async with AsyncClient(transport=ws_client, base_url="http://test") as c:
        async with aconnect_ws("http://test/agents/connect", c) as ws:
            await ws.send_text(json.dumps({"type": "auth", "payload": payload}))
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                received.update(json.loads(raw))
            except TimeoutError, Exception:  # noqa: BLE001 - closed socket => no auth_ok
                pass
    return received


async def test_ws_session_reauth_rebinds_existing_agent(ws_client, db_engine):
    """Reconnecting with a session token rebinds the same row, not a new one."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.user import Credential

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # Pre-existing agent + its long-lived session credential (as if previously
    # registered), with stale result-server coordinates.
    session_token = "dh_sess_reauth123"
    async with factory() as db:
        agent = Agent(name="reauth-agent", status="unavailable", result_host="1.1.1.1")
        db.add(agent)
        await db.flush()
        agent_id = agent.id
        db.add(Credential(agent_id=agent_id, kind="agent_session", token=session_token))
        await db.commit()

    received = await _connect_once(
        ws_client, {"token": session_token, "name": "reauth-agent", "result_port": 9009}
    )

    assert received.get("type") == "auth_ok"
    assert received["payload"]["agent_id"] == str(agent_id)
    # Re-auth returns the same long-lived token, not a freshly minted one.
    assert received["payload"]["session_token"] == session_token

    async with factory() as db:
        # No orphan: still exactly one agent row, and the session cred survives.
        assert (await db.execute(select(func.count()).select_from(Agent))).scalar_one() == 1
        creds = (
            (await db.execute(select(Credential).where(Credential.kind == "agent_session")))
            .scalars()
            .all()
        )
        assert len(creds) == 1
        rebound = await db.get(Agent, agent_id)
        assert rebound is not None
        assert rebound.status == "unavailable"  # reset on disconnect in the finally block
        # Result-server coordinates refreshed from this connection.
        assert rebound.result_port == 9009
        assert rebound.result_host


async def test_ws_consumed_bootstrap_token_is_rejected(ws_client, db_engine):
    """A bootstrap token works once; a second use is rejected with no new row."""
    from datetime import datetime, timedelta

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.user import Credential

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    token = "dh_boot_singleuse"
    async with factory() as db:
        db.add(
            Credential(
                kind="agent_bootstrap",
                token=token,
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        )
        await db.commit()

    first = await _connect_once(ws_client, {"token": token, "result_port": 8001})
    assert first.get("type") == "auth_ok"

    # The bootstrap credential is consumed; reusing it must not register again.
    second = await _connect_once(ws_client, {"token": token, "result_port": 8001})
    assert second.get("type") != "auth_ok"

    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(Agent))).scalar_one() == 1


async def test_ws_multiple_reconnects_keep_one_row(ws_client, db_engine):
    """Bootstrap once, then reconnect repeatedly with the session token."""
    from datetime import datetime, timedelta

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.user import Credential

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    boot = "dh_boot_multi"
    async with factory() as db:
        db.add(
            Credential(
                kind="agent_bootstrap",
                token=boot,
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        )
        await db.commit()

    first = await _connect_once(ws_client, {"token": boot, "result_port": 8001})
    session_token = first["payload"]["session_token"]

    for _ in range(3):
        again = await _connect_once(ws_client, {"token": session_token, "result_port": 8001})
        assert again.get("type") == "auth_ok"

    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(Agent))).scalar_one() == 1


async def _add_bootstrap(factory, token: str) -> None:
    from datetime import datetime, timedelta

    from api.models.user import Credential

    async with factory() as db:
        db.add(
            Credential(
                kind="agent_bootstrap",
                token=token,
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        )
        await db.commit()


async def test_ws_frame_error_does_not_break_next_frame(ws_client, db_engine):
    """A frame whose handling raises is logged and skipped; the per-frame session
    is fresh, so the next frame on the same socket is still processed."""
    import asyncio

    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    token = "dh_boot_frameerr"
    await _add_bootstrap(factory, token)

    caps = {"duckdb_version": "1.5.2", "memory_limit_gb": 6.0}
    async with AsyncClient(transport=ws_client, base_url="http://test") as c:
        async with aconnect_ws("http://test/agents/connect", c) as ws:
            await ws.send_text(
                json.dumps({"type": "auth", "payload": {"token": token, "result_port": 8001}})
            )
            raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
            agent_id = uuid.UUID(json.loads(raw)["payload"]["agent_id"])

            # A QUERY_DONE frame with no query_id raises KeyError inside the
            # handler; under the old shared-session design this disconnected the
            # agent and the next frame was never processed.
            await ws.send_text(json.dumps({"type": "query_done", "payload": {"status": "done"}}))
            # A valid AGENT_STATUS frame must still land after the bad frame.
            await ws.send_text(json.dumps({"type": "agent_status", "payload": caps}))
            await asyncio.sleep(0.2)

    async with factory() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        # The capabilities write proves the loop survived the failed frame.
        assert agent.capabilities == caps


async def test_ws_query_done_visible_to_separate_session(ws_client, db_engine):
    """QUERY_DONE applied in the handler's per-frame session is committed and so
    visible to a separate session (the run_sync_query poll contract)."""
    import asyncio

    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.query import Query
    from api.models.user import User
    from api.models.workspace import Workspace
    from api.services.auth import hash_password

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    token = "dh_boot_qdone"
    await _add_bootstrap(factory, token)

    async with factory() as db:
        user = User(
            email="qd@test.local", password_hash=hash_password("pw"), name="QD", role="user"
        )
        db.add(user)
        await db.flush()
        ws_row = Workspace(slug="qd-ws", name="QD WS")
        db.add(ws_row)
        await db.flush()
        query = Query(workspace_id=ws_row.id, sql="SELECT 1", status="running")
        db.add(query)
        await db.commit()
        query_id = query.id

    async with AsyncClient(transport=ws_client, base_url="http://test") as c:
        async with aconnect_ws("http://test/agents/connect", c) as ws:
            await ws.send_text(
                json.dumps({"type": "auth", "payload": {"token": token, "result_port": 8001}})
            )
            await asyncio.wait_for(ws.receive_text(), timeout=5.0)

            await ws.send_text(
                json.dumps(
                    {
                        "type": "query_done",
                        "payload": {
                            "query_id": str(query_id),
                            "status": "done",
                            "row_count": 7,
                            "duration_ms": 123,
                        },
                    }
                )
            )
            await asyncio.sleep(0.2)
            # Read in a fresh session while the socket is still open: the terminal
            # status must already be committed and visible cross-session.
            async with factory() as db:
                done = await db.get(Query, query_id)
                assert done is not None
                assert done.status == "done"
                assert done.row_count == 7
                assert done.duration_ms == 123
