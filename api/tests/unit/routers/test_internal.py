"""Network-private inter-replica dispatch endpoints."""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.config import settings
from api.main import app
from api.services.agent_registry import registry


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._connections.clear()
    yield
    registry._connections.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_send_requires_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_secret", "shared")
    resp = await client.post(f"/internal/agents/{uuid.uuid4()}/send", json={"payload": "x"})
    assert resp.status_code == 403


async def test_send_rejected_when_no_secret_configured(client, monkeypatch):
    """Even a request bearing a secret is rejected when forwarding is disabled."""
    monkeypatch.setattr(settings, "internal_api_secret", None)
    resp = await client.post(
        f"/internal/agents/{uuid.uuid4()}/send",
        json={"payload": "x"},
        headers={"X-Internal-Secret": "anything"},
    )
    assert resp.status_code == 403


async def test_send_delivers_to_local_socket(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_secret", "shared")
    agent_id = uuid.uuid4()
    ws = FakeWS()
    registry.register(agent_id, ws)
    resp = await client.post(
        f"/internal/agents/{agent_id}/send",
        json={"payload": "frame"},
        headers={"X-Internal-Secret": "shared"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"delivered": True}
    assert ws.sent == ["frame"]


async def test_send_not_delivered_when_agent_absent(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_secret", "shared")
    resp = await client.post(
        f"/internal/agents/{uuid.uuid4()}/send",
        json={"payload": "frame"},
        headers={"X-Internal-Secret": "shared"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"delivered": False}


async def test_disconnect_closes_local_socket(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_secret", "shared")
    agent_id = uuid.uuid4()
    ws = FakeWS()
    registry.register(agent_id, ws)
    resp = await client.post(
        f"/internal/agents/{agent_id}/disconnect",
        headers={"X-Internal-Secret": "shared"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"disconnected": True}
    assert ws.closed is True
