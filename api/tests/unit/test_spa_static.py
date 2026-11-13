"""SPAStaticFiles serves index.html for client-side routes but returns a real
404 for missing asset paths (so broken asset URLs aren't masked as HTML 200s)."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.config import settings
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


async def test_index_is_revalidated_on_every_load(spa_app: FastAPI):
    """A heuristically cached index.html outlives an upgrade and points at asset
    hashes the new build no longer serves, leaving a blank page."""
    for path in ("/", "/index.html", "/local/worksheets"):
        resp = await _get(spa_app, path)
        assert resp.headers["cache-control"] == "no-cache", path


async def test_hashed_assets_are_cached_for_good(spa_app: FastAPI):
    resp = await _get(spa_app, "/assets/app.js")
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_index_carries_the_content_security_policy(spa_app: FastAPI):
    """The page refuses scripts, images and requests from anywhere but its own
    origin, so injected markup can neither run nor send data out."""
    for path in ("/", "/index.html", "/local/worksheets"):
        policy = (await _get(spa_app, path)).headers["content-security-policy"]
        directives = {d.split()[0]: d for d in policy.split("; ")}
        assert directives["script-src"] == "script-src 'self'", path
        assert directives["img-src"] == "img-src 'self' data:", path
        assert directives["connect-src"] == "connect-src 'self'", path
        assert directives["frame-ancestors"] == "frame-ancestors 'none'", path


async def test_report_only_mode_reports_without_blocking(spa_app: FastAPI, monkeypatch):
    monkeypatch.setattr(settings, "csp_report_only", True)
    resp = await _get(spa_app, "/")
    assert "content-security-policy" not in resp.headers
    assert "script-src 'self'" in resp.headers["content-security-policy-report-only"]


async def test_policy_can_be_turned_off(spa_app: FastAPI, monkeypatch):
    monkeypatch.setattr(settings, "csp_enabled", False)
    resp = await _get(spa_app, "/")
    assert "content-security-policy" not in resp.headers
    assert "content-security-policy-report-only" not in resp.headers
