"""Wiring tests for the outer ASGI app: the REST API is reachable under /api
and the agent WebSocket stays at the root path."""

from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount

from api.deps import get_db
from api.main import _outer_lifespan, api_app, app


def test_api_mounted_under_api_prefix():
    assert any(isinstance(r, Mount) and r.path == "/api" and r.app is api_app for r in app.routes)


def test_agent_ws_route_stays_at_root():
    assert app.url_path_for("agent_connect") == "/agents/connect"


async def test_api_prefix_routes_reach_routers():
    async def _no_db():
        yield None

    api_app.dependency_overrides[get_db] = _no_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/auth/login", json={})
    finally:
        api_app.dependency_overrides.clear()

    # Reached the login router (body validation), not the SPA fallback / a 404.
    assert resp.status_code == 422


async def test_outer_app_exposes_the_catalog_client_the_websocket_needs(monkeypatch):
    """The agent WebSocket is on the outer app; the lifespan sets state on the inner one.

    Lineage extraction runs off a QUERY_DONE frame and reads a source table's
    columns through this client. Reaching for it on the wrong app degrades
    *silently* to table-level lineage rather than failing, which is exactly the
    kind of gap no unit test of the extractor itself would ever show.

    The real lifespan opens a database and starts background loops, so only the
    handover is exercised here: the inner context is stubbed to publish a
    sentinel, and the outer one has to carry it across.
    """
    sentinel = object()

    @asynccontextmanager
    async def _inner(_app):
        api_app.state.polaris_client = sentinel
        yield

    monkeypatch.setattr(api_app.router, "lifespan_context", _inner)

    async with _outer_lifespan(app):
        assert app.state.polaris_client is sentinel
