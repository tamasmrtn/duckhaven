from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from api.config import settings
from api.models.catalog import Catalog
from api.models.user import User
from api.services.auth import hash_password
from api.services.system_catalog.constants import SYSTEM_CATALOG_SLUG


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


async def test_create_first_admin_provisions_system_catalog(
    client: AsyncClient, setup_token: str, db_session, fake_polaris
):
    """First-admin setup provisions the built-in system catalog on the chosen
    (defaulted) storage and registers it in Polaris."""
    resp = await client.post(
        "/setup/admin",
        headers={"X-Setup-Token": setup_token},
        json={"email": "admin@test.local", "password": "longenough"},
    )
    assert resp.status_code == 200, resp.text

    catalog = (
        await db_session.execute(sa.select(Catalog).where(Catalog.is_system.is_(True)))
    ).scalar_one()
    assert catalog.slug == SYSTEM_CATALOG_SLUG
    assert catalog.created_by is not None  # the admin owns the record
    assert SYSTEM_CATALOG_SLUG in fake_polaris.catalogs
    # All three namespaces exist (query / access / info_schema).
    assert {n for (c, n) in fake_polaris.schemas if c == SYSTEM_CATALOG_SLUG} == {
        "query",
        "access",
        "info_schema",
    }


async def test_create_first_admin_with_external_storage(
    client: AsyncClient, setup_token: str, db_session
):
    resp = await client.post(
        "/setup/admin",
        headers={"X-Setup-Token": setup_token},
        json={
            "email": "admin@test.local",
            "password": "longenough",
            "system_storage": {"kind": "s3", "name": "sys", "root_uri": "s3://bucket/system"},
        },
    )
    assert resp.status_code == 200, resp.text
    catalog = (
        await db_session.execute(sa.select(Catalog).where(Catalog.is_system.is_(True)))
    ).scalar_one()
    await db_session.refresh(catalog, attribute_names=["storage_backend"])
    assert catalog.storage_backend.kind == "s3"


async def test_create_first_admin_rejects_external_storage_without_uri(
    client: AsyncClient, setup_token: str
):
    resp = await client.post(
        "/setup/admin",
        headers={"X-Setup-Token": setup_token},
        json={
            "email": "admin@test.local",
            "password": "longenough",
            "system_storage": {"kind": "s3", "name": "sys", "root_uri": ""},
        },
    )
    assert resp.status_code == 422
    # The bad choice is rejected before any admin user is created.
    assert settings.setup_token_path.exists()


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
