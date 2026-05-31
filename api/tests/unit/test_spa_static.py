"""SPAStaticFiles serves index.html for client-side routes but returns a real
404 for missing asset paths (so broken asset URLs aren't masked as HTML 200s)."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.main import SPAStaticFiles


@pytest.fixture
def spa_app(tmp_path: Path) -> FastAPI:
    (tmp_path / "index.html").write_text("<!doctype html><title>spa</title>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hi')")

    app = FastAPI()
    app.mount("/", SPAStaticFiles(directory=str(tmp_path), html=True), name="ui")
    return app


async def _get(app: FastAPI, path: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(path)


async def test_real_asset_is_served(spa_app: FastAPI):
    resp = await _get(spa_app, "/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


async def test_missing_asset_returns_404(spa_app: FastAPI):
    # A missing file with an extension must 404, not return index.html as 200.
    resp = await _get(spa_app, "/assets/files/inter-latin-400-normal.woff2")
    assert resp.status_code == 404
    assert "<!doctype html>" not in resp.text.lower()


async def test_client_route_falls_back_to_index(spa_app: FastAPI):
    # Extensionless deep links are SPA routes and should serve index.html.
    resp = await _get(spa_app, "/local/admin/users")
    assert resp.status_code == 200
    assert "<title>spa</title>" in resp.text
