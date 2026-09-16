import pytest
from conftest import seed_workspace
from httpx import AsyncClient

from api.config import settings
from api.deps import get_db, get_polaris_client
from api.main import api_app
from api.models.user import User
from api.routers.health import API_VERSION
from api.services.auth import hash_password


async def test_healthz_ok(client: AsyncClient):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_version_ok(client: AsyncClient):
    resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body.keys() == {"version", "api_version"}
    assert body["api_version"] == API_VERSION


async def test_version_reflects_app_version(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_version", "9.9.9")
    resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json()["version"] == "9.9.9"


async def test_readyz_ok(client: AsyncClient):
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_readyz_503_when_draining(client: AsyncClient):
    api_app.state.draining = True
    try:
        resp = await client.get("/readyz")
    finally:
        api_app.state.draining = False
    assert resp.status_code == 503
    assert resp.json()["message"] == "draining"


class _BrokenPolaris:
    async def ping(self):
        raise RuntimeError("connection refused")


async def test_readyz_503_when_polaris_unreachable(client: AsyncClient, db_session):
    """Polaris is a readiness dependency when an Iceberg catalog exists."""
    owner = User(email="ready@test.local", password_hash=hash_password("pw"), name="R", role="user")
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)
    await seed_workspace(db_session, user_id=owner.id, slug="ice-ws", catalog_slug="ice_cat")

    api_app.dependency_overrides[get_polaris_client] = lambda: _BrokenPolaris()
    try:
        resp = await client.get("/readyz")
    finally:
        del api_app.dependency_overrides[get_polaris_client]
    assert resp.status_code == 503
    assert "polaris unreachable" in resp.json()["message"]


async def test_readyz_ignores_polaris_when_no_iceberg_catalog_exists(client: AsyncClient):
    """A DuckLake-only deployment does not run Polaris at all. Pinging it
    unconditionally would keep such a stack permanently un-ready.

    Deliberately keyed on what is deployed (does an Iceberg catalog exist?)
    rather than on a feature flag, so the answer cannot disagree with reality."""
    api_app.dependency_overrides[get_polaris_client] = lambda: _BrokenPolaris()
    try:
        resp = await client.get("/readyz")
    finally:
        del api_app.dependency_overrides[get_polaris_client]
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


async def test_healthz_503_when_db_unreachable(client: AsyncClient):
    async def broken_db():
        class _S:
            async def execute(self, _):
                raise RuntimeError("connection refused")

        yield _S()

    api_app.dependency_overrides[get_db] = broken_db
    try:
        resp = await client.get("/healthz")
    finally:
        # client fixture's teardown also clears, but be explicit about scope.
        del api_app.dependency_overrides[get_db]
    assert resp.status_code == 503
    assert "database unreachable" in resp.json()["message"]
