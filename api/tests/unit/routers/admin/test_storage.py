import pytest
from httpx import AsyncClient

from api.models.user import User
from api.services.auth import hash_password


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


async def test_create_object_store_backend(admin_client: AsyncClient):
    resp = await admin_client.post(
        "/admin/storage-backends",
        json={"kind": "object_store", "name": "primary", "root_uri": ""},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["kind"] == "object_store"
    assert data["workspace_count"] == 0


async def test_create_invalid_kind(admin_client: AsyncClient):
    resp = await admin_client.post(
        "/admin/storage-backends",
        json={"kind": "hdfs", "name": "bad", "root_uri": "hdfs://"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("kind", ["local_fs", "nas"])
async def test_legacy_kinds_no_longer_creatable(admin_client: AsyncClient, kind: str):
    """local_fs/nas collapsed into object_store; they can't be created anymore."""
    resp = await admin_client.post(
        "/admin/storage-backends",
        json={"kind": kind, "name": "legacy", "root_uri": "file:///x"},
    )
    assert resp.status_code == 422


async def test_delete_unused_backend(admin_client: AsyncClient):
    create = await admin_client.post(
        "/admin/storage-backends",
        json={"kind": "object_store", "name": "todel", "root_uri": "del/"},
    )
    backend_id = create.json()["id"]
    resp = await admin_client.delete(f"/admin/storage-backends/{backend_id}")
    assert resp.status_code == 204


async def test_delete_used_backend_fails(admin_client: AsyncClient, admin: User, db_session):
    from api.models.catalog import Catalog
    from api.models.storage_backend import StorageBackend

    sb = StorageBackend(kind="object_store", name="used", root_uri="used/", created_by=admin.id)
    db_session.add(sb)
    await db_session.flush()
    # A backend is "in use" when a catalog references it.
    cat = Catalog(
        slug="occupied",
        name="Occupied",
        polaris_name="occupied",
        storage_backend_id=sb.id,
        created_by=admin.id,
    )
    db_session.add(cat)
    await db_session.commit()

    resp = await admin_client.delete(f"/admin/storage-backends/{sb.id}")
    assert resp.status_code == 409


async def test_non_admin_blocked(user_client: AsyncClient):
    resp = await user_client.get("/admin/storage-backends")
    assert resp.status_code == 403
