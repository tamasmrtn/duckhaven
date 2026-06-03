import pytest
from httpx import AsyncClient

from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
async def admin(db_session):
    u = User(
        email="admin@users.local",
        password_hash=hash_password("pw"),
        name="Admin",
        role="admin",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def regular_user(db_session):
    u = User(
        email="user@users.local",
        password_hash=hash_password("pw"),
        name="Regular",
        role="user",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User):
    await client.post("/auth/login", json={"email": "admin@users.local", "password": "pw"})
    return client


@pytest.fixture
async def user_client(client: AsyncClient, regular_user: User):
    await client.post("/auth/login", json={"email": "user@users.local", "password": "pw"})
    return client


async def test_list_users_returns_registered_users(
    admin_client: AsyncClient, admin: User, regular_user: User
):
    resp = await admin_client.get("/admin/users")
    assert resp.status_code == 200
    data = resp.json()
    emails = {row["email"] for row in data}
    assert emails == {"admin@users.local", "user@users.local"}
    # Real fields, not mock fixtures.
    admin_row = next(r for r in data if r["email"] == "admin@users.local")
    assert admin_row["role"] == "admin"
    assert admin_row["id"] == str(admin.id)
    assert "password_hash" not in admin_row


async def test_list_users_ordered_by_created_at(
    admin_client: AsyncClient, admin: User, regular_user: User
):
    resp = await admin_client.get("/admin/users")
    assert resp.status_code == 200
    emails = [row["email"] for row in resp.json()]
    # admin fixture is created before regular_user.
    assert emails.index("admin@users.local") < emails.index("user@users.local")


async def test_list_users_non_admin_forbidden(user_client: AsyncClient):
    resp = await user_client.get("/admin/users")
    assert resp.status_code == 403


async def test_list_users_unauthenticated(client: AsyncClient):
    resp = await client.get("/admin/users")
    assert resp.status_code == 401
