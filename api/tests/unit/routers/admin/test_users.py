import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import delete as sa_delete

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
    data = resp.json()["items"]
    emails = {row["email"] for row in data}
    assert emails == {"admin@users.local", "user@users.local"}
    # Real fields, not mock fixtures.
    admin_row = next(r for r in data if r["email"] == "admin@users.local")
    assert admin_row["role"] == "admin"
    assert admin_row["id"] == str(admin.id)
    assert "password_hash" not in admin_row


async def test_list_users_is_totally_ordered(
    admin_client: AsyncClient, admin: User, regular_user: User
):
    """Ordered by (created_at, id), not created_at alone.

    The id tiebreak is what makes the order total. Without it, rows sharing a
    timestamp -- and `created_at` is only second-precision -- can come back in a
    different order on each call, which for a paged endpoint means a row served
    on two pages or on none.
    """
    first = await admin_client.get("/admin/users")
    again = await admin_client.get("/admin/users")
    assert first.status_code == 200
    emails = [row["email"] for row in first.json()["items"]]
    assert emails == [row["email"] for row in again.json()["items"]]
    assert set(emails) == {"admin@users.local", "user@users.local"}


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


async def test_list_users_pages_with_a_cursor(
    admin_client: AsyncClient, admin: User, regular_user: User
):
    """The envelope pages: a limit of one returns one row and a cursor that
    fetches the next, and the last page reports no more."""
    first = await admin_client.get("/admin/users", params={"limit": 1})
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 1
    assert body["has_more"] is True
    assert body["cursor"]

    second = await admin_client.get("/admin/users", params={"limit": 1, "cursor": body["cursor"]})
    assert second.status_code == 200
    rest = second.json()
    assert len(rest["items"]) == 1
    assert rest["has_more"] is False
    assert rest["cursor"] is None
    assert rest["items"][0]["email"] != body["items"][0]["email"]


async def test_list_users_rejects_a_malformed_cursor(admin_client: AsyncClient, admin: User):
    resp = await admin_client.get("/admin/users", params={"cursor": "not-a-cursor"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_cursor"


async def test_paging_yields_every_row_exactly_once(
    admin_client: AsyncClient, admin: User, regular_user: User, db_session
):
    """Walking the cursor must cover the collection with no gap and no repeat.

    The fixtures share a `created_at` -- it is second-precision -- so this
    exercises the tie-break specifically. An earlier cursor carried the sort
    values themselves, which meant comparing a bound Python timestamp against a
    stored one; SQLite writes CURRENT_TIMESTAMP without microseconds and
    SQLAlchemy binds them, so the tie never matched and the second page came
    back empty.
    """
    for n in range(4):
        db_session.add(
            User(
                email=f"extra{n}@users.local",
                password_hash=hash_password("pw"),
                name=f"Extra {n}",
                role="user",
            )
        )
    await db_session.commit()

    expected = [u["email"] for u in (await admin_client.get("/admin/users")).json()["items"]]

    walked: list[str] = []
    cursor: str | None = None
    for _ in range(len(expected) + 2):
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = (await admin_client.get("/admin/users", params=params)).json()
        walked += [u["email"] for u in body["items"]]
        cursor = body["cursor"]
        if not body["has_more"]:
            break

    assert walked == expected


async def test_a_cursor_whose_row_was_deleted_is_an_error(
    admin_client: AsyncClient, admin: User, regular_user: User, db_session
):
    """Deleting the row a cursor points at must not read as "end of list".

    The predicate reads the anchor's sort values back out of the table, so a
    missing anchor matches nothing — which is indistinguishable from a last page
    unless the server says otherwise. Silently dropping the rest of the
    collection is the worst available answer, because the client cannot tell.
    """
    # Stamped explicitly: `created_at` is second-precision, so without this the
    # fixtures tie and the id tiebreak decides the order -- which can put the
    # admin at the page boundary and have this test delete its own session.
    later = datetime(2030, 1, 1, tzinfo=UTC)
    for n in range(3):
        db_session.add(
            User(
                email=f"doomed{n}@users.local",
                password_hash=hash_password("pw"),
                name=f"Doomed {n}",
                role="user",
                created_at=later + timedelta(minutes=n),
            )
        )
    await db_session.commit()

    body = (await admin_client.get("/admin/users", params={"limit": 3})).json()
    assert body["has_more"] is True
    anchor = body["items"][-1]["id"]
    assert anchor != str(admin.id), "the anchor must not be the caller's own account"

    await db_session.execute(sa_delete(User).where(User.id == uuid.UUID(anchor)))
    await db_session.commit()

    resp = await admin_client.get("/admin/users", params={"limit": 3, "cursor": body["cursor"]})
    assert resp.status_code == 422
    assert resp.json()["error"] == "stale_cursor"
