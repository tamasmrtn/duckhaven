"""Wiring tests for the outer ASGI app: the REST API is reachable under /api
and the agent WebSocket stays at the root path."""

from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount

from api.deps import get_db
from api.main import api_app, app


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
