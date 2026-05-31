from pathlib import Path

import pytest
from httpx import AsyncClient

from api.config import settings
from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
def setup_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the setup-token path at a tempfile holding a known token."""
    token = "test-setup-token-abc123"
    path = tmp_path / "setup_token"
    path.write_text(token)
    monkeypatch.setattr(settings, "setup_token_path", path)
    return token


async def test_status_true_on_empty_db(client: AsyncClient):
    resp = await client.get("/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"needs_admin": True}


async def test_status_false_after_admin_exists(client: AsyncClient, db_session):
    db_session.add(
        User(
            email="existing@test.local",
            password_hash=hash_password("xxxxxxxx"),
            name="Pre-existing",
            role="admin",
        )
    )
    await db_session.commit()

    resp = await client.get("/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"needs_admin": False}


async def test_create_first_admin_success(client: AsyncClient, setup_token: str):
    resp = await client.post(
        "/setup/admin",
        headers={"X-Setup-Token": setup_token},
        json={"email": "admin@test.local", "password": "longenough"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email"] == "admin@test.local"
    assert data["role"] == "admin"
    assert "session" in resp.cookies
    # Token file must be consumed.
    assert not settings.setup_token_path.exists()


async def test_create_first_admin_rejects_missing_token(client: AsyncClient, setup_token: str):
    resp = await client.post(
        "/setup/admin",
        json={"email": "admin@test.local", "password": "longenough"},
    )
    assert resp.status_code == 403
    assert settings.setup_token_path.exists()


async def test_create_first_admin_rejects_wrong_token(client: AsyncClient, setup_token: str):
    resp = await client.post(
        "/setup/admin",
        headers={"X-Setup-Token": "wrong"},
        json={"email": "admin@test.local", "password": "longenough"},
    )
    assert resp.status_code == 403
    assert settings.setup_token_path.exists()


async def test_create_first_admin_rejects_when_users_exist(
    client: AsyncClient, setup_token: str, db_session
):
    db_session.add(
        User(
            email="existing@test.local",
            password_hash=hash_password("xxxxxxxx"),
            name="Pre-existing",
            role="admin",
        )
    )
    await db_session.commit()

    resp = await client.post(
        "/setup/admin",
        headers={"X-Setup-Token": setup_token},
        json={"email": "admin@test.local", "password": "longenough"},
    )
    assert resp.status_code == 409
    # Token file is NOT consumed when the gate fails on user count.
    assert settings.setup_token_path.exists()


async def test_create_first_admin_rejects_when_token_file_missing(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "setup_token_path", tmp_path / "does-not-exist")
    resp = await client.post(
        "/setup/admin",
        headers={"X-Setup-Token": "any"},
        json={"email": "admin@test.local", "password": "longenough"},
    )
    assert resp.status_code == 403
