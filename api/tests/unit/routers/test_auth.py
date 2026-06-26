import pytest
from httpx import AsyncClient

from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
async def admin_user(db_session):
    user = User(
        email="admin@test.local",
        password_hash=hash_password("secret"),
        name="Admin",
        role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_login_ok(client: AsyncClient, admin_user: User):
    resp = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@test.local"
    assert "session" in resp.cookies


async def test_login_wrong_password(client: AsyncClient, admin_user: User):
    resp = await client.post("/auth/login", json={"email": "admin@test.local", "password": "wrong"})
    assert resp.status_code == 401


async def test_login_unknown_email(client: AsyncClient):
    resp = await client.post("/auth/login", json={"email": "nobody@test.local", "password": "x"})
    assert resp.status_code == 401


async def test_me_with_session(client: AsyncClient, admin_user: User):
    login = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert login.status_code == 200
    resp = await client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@test.local"


async def test_me_no_cookie(client: AsyncClient):
    resp = await client.get("/me")
    assert resp.status_code == 401


async def test_logout_clears_session(client: AsyncClient, admin_user: User):
    await client.post("/auth/login", json={"email": "admin@test.local", "password": "secret"})
    resp = await client.post("/auth/logout")
    assert resp.status_code == 204
    me = await client.get("/me")
    assert me.status_code == 401


async def test_login_returns_permissions(client: AsyncClient, admin_user: User):
    resp = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert resp.status_code == 200
    assert "users:manage" in resp.json()["permissions"]


async def test_methods_local_only_by_default(client: AsyncClient):
    resp = await client.get("/auth/methods")
    assert resp.status_code == 200
    data = resp.json()
    assert data["local"] is True
    assert data["ldap"] is False
    assert data["oidc"] is False


async def test_inactive_user_cannot_login(client: AsyncClient, db_session, admin_user: User):
    admin_user.is_active = False
    db_session.add(admin_user)
    await db_session.commit()
    resp = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert resp.status_code == 401


async def test_deactivating_user_blocks_live_session(
    client: AsyncClient, db_session, admin_user: User
):
    await client.post("/auth/login", json={"email": "admin@test.local", "password": "secret"})
    assert (await client.get("/me")).status_code == 200
    admin_user.is_active = False
    db_session.add(admin_user)
    await db_session.commit()
    assert (await client.get("/me")).status_code == 401
