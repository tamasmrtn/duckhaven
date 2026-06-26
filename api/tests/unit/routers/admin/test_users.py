import pytest
from conftest import seed_workspace
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


async def test_create_user(admin_client: AsyncClient):
    resp = await admin_client.post(
        "/admin/users",
        json={
            "email": "new@users.local",
            "name": "New",
            "password": "pw",
            "role": "user",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "new@users.local"
    assert data["role"] == "user"
    assert data["auth_provider"] == "local"
    assert data["is_active"] is True


async def test_create_user_duplicate_email_conflict(admin_client: AsyncClient, admin: User):
    resp = await admin_client.post(
        "/admin/users",
        json={"email": "admin@users.local", "name": "Dup", "password": "pw", "role": "user"},
    )
    assert resp.status_code == 409


async def test_create_user_unknown_role_rejected(admin_client: AsyncClient):
    resp = await admin_client.post(
        "/admin/users",
        json={"email": "x@users.local", "name": "X", "password": "pw", "role": "wizard"},
    )
    assert resp.status_code == 422


async def test_create_user_non_admin_forbidden(user_client: AsyncClient):
    resp = await user_client.post(
        "/admin/users",
        json={"email": "x@users.local", "name": "X", "password": "pw", "role": "user"},
    )
    assert resp.status_code == 403


async def test_update_user_role(admin_client: AsyncClient, regular_user: User):
    resp = await admin_client.patch(f"/admin/users/{regular_user.id}", json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


async def test_update_user_deactivate(admin_client: AsyncClient, regular_user: User):
    resp = await admin_client.patch(f"/admin/users/{regular_user.id}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_cannot_demote_last_admin(admin_client: AsyncClient, admin: User):
    resp = await admin_client.patch(f"/admin/users/{admin.id}", json={"role": "user"})
    assert resp.status_code == 409


async def test_cannot_deactivate_last_admin(admin_client: AsyncClient, admin: User):
    resp = await admin_client.patch(f"/admin/users/{admin.id}", json={"is_active": False})
    assert resp.status_code == 409


async def test_revoke_sessions_invalidates_session(
    admin_client: AsyncClient, db_session, regular_user: User
):
    from api.services.auth import create_session, get_session_user

    token = await create_session(db_session, regular_user.id)
    resp = await admin_client.post(f"/admin/users/{regular_user.id}/revoke-sessions")
    assert resp.status_code == 204
    db_session.expire_all()
    assert await get_session_user(db_session, token) is None


# --- Workspace membership management ---


async def test_list_user_workspaces_includes_non_member(
    admin_client: AsyncClient, admin: User, regular_user: User, db_session
):
    await seed_workspace(db_session, user_id=admin.id, slug="alpha", name="Alpha")
    resp = await admin_client.get(f"/admin/users/{regular_user.id}/workspaces")
    assert resp.status_code == 200
    alpha = next(r for r in resp.json() if r["slug"] == "alpha")
    assert alpha["role"] is None


async def test_set_user_workspace_role_adds_then_updates(
    admin_client: AsyncClient, admin: User, regular_user: User, db_session
):
    await seed_workspace(db_session, user_id=admin.id, slug="alpha", name="Alpha")
    add = await admin_client.put(
        f"/admin/users/{regular_user.id}/workspaces/alpha", json={"role": "reader"}
    )
    assert add.status_code == 200
    assert add.json()["role"] == "reader"

    upd = await admin_client.put(
        f"/admin/users/{regular_user.id}/workspaces/alpha", json={"role": "writer"}
    )
    assert upd.json()["role"] == "writer"

    listing = await admin_client.get(f"/admin/users/{regular_user.id}/workspaces")
    assert next(r for r in listing.json() if r["slug"] == "alpha")["role"] == "writer"


async def test_set_user_workspace_invalid_role_rejected(
    admin_client: AsyncClient, admin: User, regular_user: User, db_session
):
    await seed_workspace(db_session, user_id=admin.id, slug="alpha", name="Alpha")
    resp = await admin_client.put(
        f"/admin/users/{regular_user.id}/workspaces/alpha", json={"role": "superuser"}
    )
    assert resp.status_code == 422


async def test_remove_user_from_workspace(
    admin_client: AsyncClient, admin: User, regular_user: User, db_session
):
    await seed_workspace(db_session, user_id=admin.id, slug="alpha", name="Alpha")
    await admin_client.put(
        f"/admin/users/{regular_user.id}/workspaces/alpha", json={"role": "reader"}
    )
    rm = await admin_client.delete(f"/admin/users/{regular_user.id}/workspaces/alpha")
    assert rm.status_code == 204
    listing = await admin_client.get(f"/admin/users/{regular_user.id}/workspaces")
    assert next(r for r in listing.json() if r["slug"] == "alpha")["role"] is None


async def test_workspace_membership_requires_users_manage(
    user_client: AsyncClient, admin: User, regular_user: User, db_session
):
    await seed_workspace(db_session, user_id=admin.id, slug="alpha", name="Alpha")
    resp = await user_client.get(f"/admin/users/{regular_user.id}/workspaces")
    assert resp.status_code == 403
