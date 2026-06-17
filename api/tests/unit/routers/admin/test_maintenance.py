from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.deps import get_session_factory
from api.main import api_app
from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
async def admin(db_session) -> User:
    u = User(email="a@test.local", password_hash=hash_password("pw"), name="A", role="admin")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def regular(db_session) -> User:
    u = User(email="r@test.local", password_hash=hash_password("pw"), name="R", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User, db_engine) -> AsyncClient:
    # The scan endpoint resolves the session factory itself; point it at the
    # test engine so a manual scan runs against the in-memory DB.
    api_app.dependency_overrides[get_session_factory] = lambda: async_sessionmaker(
        db_engine, expire_on_commit=False
    )
    await client.post("/auth/login", json={"email": "a@test.local", "password": "pw"})
    return client


async def test_get_policy_creates_defaults(admin_client: AsyncClient):
    resp = await admin_client.get("/admin/maintenance/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["preset"] == "balanced"
    assert data["scan_frequency"] == "daily"
    assert data["thresholds"]["target_file_bytes"] > 0


async def test_update_preset_reresolves_thresholds(admin_client: AsyncClient):
    resp = await admin_client.put("/admin/maintenance/policy", json={"preset": "aggressive"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["preset"] == "aggressive"
    # aggressive flags small files much earlier than balanced (0.30).
    assert data["thresholds"]["small_file_ratio_warn"] == 0.15


async def test_update_advanced_override(admin_client: AsyncClient):
    resp = await admin_client.put(
        "/admin/maintenance/policy",
        json={"thresholds": {"snapshot_count_warn": 42}},
    )
    assert resp.status_code == 200
    assert resp.json()["thresholds"]["snapshot_count_warn"] == 42


async def test_update_rejects_bad_frequency(admin_client: AsyncClient):
    resp = await admin_client.put("/admin/maintenance/policy", json={"scan_frequency": "weekly"})
    assert resp.status_code == 422


async def test_trigger_scan_runs(admin_client: AsyncClient):
    # No catalogs seeded in fake Polaris -> a clean run with nothing to dispatch.
    resp = await admin_client.post("/admin/maintenance/scan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ran"
    assert resp.json()["dispatched"] == 0


async def test_policy_requires_admin(client: AsyncClient, regular: User):
    await client.post("/auth/login", json={"email": "r@test.local", "password": "pw"})
    assert (await client.get("/admin/maintenance/policy")).status_code == 403
    assert (await client.post("/admin/maintenance/scan")).status_code == 403
