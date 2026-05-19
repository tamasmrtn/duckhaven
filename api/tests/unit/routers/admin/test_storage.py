import pytest
from api.models.user import User
from api.services.auth import hash_password
from httpx import AsyncClient


@pytest.fixture
async def admin(db_session):
    u = User(
        email="admin@test.local", password_hash=hash_password("pw"), name="Admin", role="admin"
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def regular_user(db_session):
    u = User(email="user@test.local", password_hash=hash_password("pw"), name="User", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User):
    await client.post("/auth/login", json={"email": "admin@test.local", "password": "pw"})
    return client


@pytest.fixture
async def user_client(client: AsyncClient, regular_user: User):
    await client.post("/auth/login", json={"email": "user@test.local", "password": "pw"})
    return client


# --- Storage backend tests ---


async def test_list_backends_empty(admin_client: AsyncClient):
    resp = await admin_client.get("/admin/storage-backends")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_local_fs_backend(admin_client: AsyncClient):
    resp = await admin_client.post(
        "/admin/storage-backends",
        json={"kind": "local_fs", "name": "local", "root_uri": "file:///var/data"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["kind"] == "local_fs"
    assert data["workspace_count"] == 0


async def test_create_invalid_kind(admin_client: AsyncClient):
    resp = await admin_client.post(
        "/admin/storage-backends",
        json={"kind": "hdfs", "name": "bad", "root_uri": "hdfs://"},
    )
    assert resp.status_code == 422


async def test_delete_unused_backend(admin_client: AsyncClient):
    create = await admin_client.post(
        "/admin/storage-backends",
        json={"kind": "local_fs", "name": "todel", "root_uri": "file:///tmp/del"},
    )
    backend_id = create.json()["id"]
    resp = await admin_client.delete(f"/admin/storage-backends/{backend_id}")
    assert resp.status_code == 204


async def test_delete_used_backend_fails(admin_client: AsyncClient, admin: User, db_session):
    from api.models.storage_backend import StorageBackend
    from api.models.workspace import Workspace

    sb = StorageBackend(kind="local_fs", name="used", root_uri="file:///used", created_by=admin.id)
    db_session.add(sb)
    await db_session.flush()
    ws = Workspace(slug="occupied", name="Occupied", storage_backend_id=sb.id)
    db_session.add(ws)
    await db_session.commit()

    resp = await admin_client.delete(f"/admin/storage-backends/{sb.id}")
    assert resp.status_code == 409


async def test_non_admin_blocked(user_client: AsyncClient):
    resp = await user_client.get("/admin/storage-backends")
    assert resp.status_code == 403
