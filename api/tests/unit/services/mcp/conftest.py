"""Fixtures for the MCP endpoint: a live server, and a token to reach it with.

The endpoint is built per test rather than reusing ``api.main``'s: the transport's
session manager can be run exactly once per instance, so sharing one would let the
first test that starts it break every later one.

The loopback the middleware opens targets ``api_app``, so the ``client`` fixture's
dependency overrides (SQLite, the fake Polaris) apply to it unchanged — these tests
exercise the real governed path, not a stub of it.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from api.models.user import User
from api.services.auth import hash_password
from api.services.mcp.server import MCP_PATH, build_endpoint

PROTOCOL_VERSION = "2026-07-28"

#: The `_meta` block the 2026-07-28 revision requires on every request's params.
META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "duckhaven-tests", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def rpc_headers(method: str, name: str | None = None) -> dict[str, str]:
    """The transport's required request-metadata headers, mirrored from the body."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def rpc_body(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**(params or {}), "_meta": META},
    }


def call_tool(name: str, arguments: dict | None = None) -> tuple[dict, dict]:
    """Headers and body for a ``tools/call`` of ``name``."""
    return (
        rpc_headers("tools/call", name),
        rpc_body("tools/call", {"name": name, "arguments": arguments or {}}),
    )


@pytest.fixture
async def mcp_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """A client for a freshly built MCP endpoint, hosted at the real ``/mcp`` path.

    Depends on ``client`` for its side effect: that fixture installs the
    dependency overrides the middleware's in-process loopback into ``api_app``
    relies on.

    The session manager runs in a task of its own rather than in an ``async with``
    here, because its task group has to be entered and exited by the same task and
    pytest-asyncio finalizes a fixture in a different one than it started.
    """
    server, asgi = build_endpoint()
    host = Starlette(routes=[Route(MCP_PATH, endpoint=asgi)])

    running, stop = asyncio.Event(), asyncio.Event()

    async def serve() -> None:
        async with server.session_manager.run():
            running.set()
            await stop.wait()

    task = asyncio.create_task(serve())
    await running.wait()
    try:
        transport = ASGITransport(app=host)
        async with AsyncClient(transport=transport, base_url="http://test") as mcp:
            yield mcp
    finally:
        stop.set()
        await task


@pytest.fixture
async def member(db_session) -> User:
    """A plain user who will be given workspace membership by the tests."""
    user = User(
        email="mcp-user@test.local",
        password_hash=hash_password("secret"),
        name="MCP User",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def token(client: AsyncClient, member: User) -> str:
    """A personal access token for ``member``, minted the way a person would.

    Through ``POST /me/pats`` rather than by inserting a credential row, so the
    tests hold a token the product actually issues.
    """
    login = await client.post(
        "/auth/login", json={"email": "mcp-user@test.local", "password": "secret"}
    )
    assert login.status_code == 200, login.text
    resp = await client.post("/me/pats", json={"expires_in_days": 30})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
