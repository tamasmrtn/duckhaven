import pytest
from httpx import AsyncClient

from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
async def user(db_session):
    u = User(
        email="owner@test.local", password_hash=hash_password("pw"), name="Owner", role="admin"
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def backend(db_session, user):
    sb = StorageBackend(
        kind="local_fs",
        name="local",
        root_uri="file:///var/duckhaven/data",
        created_by=user.id,
    )
    db_session.add(sb)
    await db_session.commit()
    await db_session.refresh(sb)
    return sb


@pytest.fixture
async def auth_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "owner@test.local", "password": "pw"})
    return client


async def test_list_workspaces_empty(auth_client: AsyncClient):
    resp = await auth_client.get("/workspaces")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_and_list_workspace(auth_client: AsyncClient, backend: StorageBackend):
    resp = await auth_client.post(
        "/workspaces",
        json={"slug": "myws", "name": "My WS", "storage_backend_id": str(backend.id)},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "myws"

    list_resp = await auth_client.get("/workspaces")
    assert len(list_resp.json()) == 1


async def test_create_duplicate_slug(auth_client: AsyncClient, backend: StorageBackend):
    await auth_client.post(
        "/workspaces",
        json={"slug": "dup", "name": "First", "storage_backend_id": str(backend.id)},
    )
    resp = await auth_client.post(
        "/workspaces",
        json={"slug": "dup", "name": "Second", "storage_backend_id": str(backend.id)},
    )
    assert resp.status_code == 409


async def test_get_workspace(auth_client: AsyncClient, backend: StorageBackend):
    create = await auth_client.post(
        "/workspaces",
        json={"slug": "ws1", "name": "WS1", "storage_backend_id": str(backend.id)},
    )
    slug = create.json()["slug"]
    resp = await auth_client.get(f"/workspaces/{slug}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "ws1"


async def test_get_workspace_not_found(auth_client: AsyncClient):
    resp = await auth_client.get("/workspaces/nonexistent")
    assert resp.status_code == 404


async def test_unauthenticated_blocked(client: AsyncClient, backend: StorageBackend):
    resp = await client.get("/workspaces")
    assert resp.status_code == 401


async def test_get_workspace_by_uuid(auth_client: AsyncClient, backend: StorageBackend):
    create = await auth_client.post(
        "/workspaces",
        json={"slug": "uuid-ws", "name": "UUID WS", "storage_backend_id": str(backend.id)},
    )
    ws_id = create.json()["id"]
    resp = await auth_client.get(f"/workspaces/{ws_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == ws_id


async def test_list_members(auth_client: AsyncClient, backend: StorageBackend):
    create = await auth_client.post(
        "/workspaces",
        json={"slug": "members-ws", "name": "Members WS", "storage_backend_id": str(backend.id)},
    )
    ws_slug = create.json()["slug"]
    resp = await auth_client.get(f"/workspaces/{ws_slug}/members")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["role"] == "owner"


async def test_add_member(auth_client: AsyncClient, backend: StorageBackend, db_session):
    from api.models.user import User
    from api.services.auth import hash_password

    target = User(
        email="newmember@test.local",
        password_hash=hash_password("pw"),
        name="New Member",
        role="user",
    )
    db_session.add(target)
    await db_session.commit()
    await db_session.refresh(target)

    create = await auth_client.post(
        "/workspaces",
        json={"slug": "invite-ws", "name": "Invite WS", "storage_backend_id": str(backend.id)},
    )
    ws_slug = create.json()["slug"]
    resp = await auth_client.post(
        f"/workspaces/{ws_slug}/members",
        json={"user_id": str(target.id), "role": "reader"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "reader"
