"""Wiring tests for the outer ASGI app: the REST API is reachable under /api,
the MCP endpoint sits at /mcp, and the agent WebSocket stays at the root path."""

import os
import subprocess
import sys
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount, Route

from api.deps import get_db
from api.main import _outer_lifespan, api_app, app
from api.services.mcp.server import MCP_PATH, mcp_asgi_app


def test_api_mounted_under_api_prefix():
    assert any(isinstance(r, Mount) and r.path == "/api" and r.app is api_app for r in app.routes)


def test_agent_ws_route_stays_at_root():
    assert app.url_path_for("agent_connect") == "/agents/connect"


def test_mcp_is_an_exact_route_not_a_mount():
    """A Mount only matches paths *below* its prefix.

    Mounting would turn `POST /mcp` — the URL in every client config and every
    docs example — into a 307 to `/mcp/`, which not all clients follow on a POST.
    """
    routes = [r for r in app.routes if isinstance(r, Route) and r.path == MCP_PATH]
    assert len(routes) == 1
    assert routes[0].app is mcp_asgi_app


def test_mcp_also_answers_on_the_trailing_slash_form():
    """One stray character must not look like a broken server.

    `Route("/mcp")` does not match `/mcp/`, so without this the SPA catch-all
    takes it: 405 for the POST a client actually makes, and index.html with a
    200 for a GET. Both read as "the server is there and broken" rather than
    "the URL has an extra character".
    """
    routes = [r for r in app.routes if isinstance(r, Route) and r.path == f"{MCP_PATH}/"]
    assert len(routes) == 1
    assert routes[0].app is mcp_asgi_app


#: Resolves `POST /mcp` against the real app the way Starlette's router does,
#: and reports which route wins. Run in a subprocess so `STATIC_DIR` is set
#: before `api.main` is imported (the SPA mount happens at import time).
_RESOLVE_MCP = """
import os
from starlette.routing import Match
from api.config import settings
from api.main import app
from api.services.mcp.server import MCP_PATH, mcp_asgi_app

assert settings.static_dir.is_dir(), "the SPA must be mounted for this to mean anything"
scope = {"type": "http", "method": "POST", "path": MCP_PATH, "root_path": "", "headers": []}
for route in app.routes:
    if route.matches(scope)[0] == Match.FULL:
        print("MCP" if getattr(route, "app", None) is mcp_asgi_app else type(route).__name__)
        break
else:
    print("NO_MATCH")
"""


def test_mcp_is_not_shadowed_by_the_spa_catch_all(tmp_path):
    """In the image the SPA is mounted at the root and would swallow /mcp.

    Built in a subprocess with `STATIC_DIR` pointed at a stand-in SPA, because
    the mount happens when `api.main` is imported and only when a built SPA is
    on disk — true in the image, false in this suite. Importing the app here
    therefore exercises the one arrangement in which the ordering cannot go
    wrong, and an in-process check of the route list passes whatever the order.

    Worth a subprocess because the failure is not subtle: with the catch-all
    first, `POST /mcp` answers 405 and `GET /mcp` returns index.html, so every
    MCP client sees a server that is plainly there and plainly broken.
    """
    (tmp_path / "index.html").write_text("<html></html>")
    result = subprocess.run(
        [sys.executable, "-c", _RESOLVE_MCP],
        capture_output=True,
        text=True,
        env={**os.environ, "STATIC_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "MCP", result.stdout


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
