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


async def test_create_and_list_workspace(
    auth_client: AsyncClient, backend: StorageBackend, fake_uc
):
    resp = await auth_client.post(
        "/workspaces",
        json={"slug": "myws", "name": "My WS", "storage_backend_id": str(backend.id)},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "myws"
    # WorkspaceOut surfaces the backend kind (the web UI renders it).
    assert data["storage_backend_kind"] == "local_fs"

    list_resp = await auth_client.get("/workspaces")
    listed = list_resp.json()
    assert len(listed) == 1
    assert listed[0]["storage_backend_kind"] == "local_fs"

    detail_resp = await auth_client.get("/workspaces/myws")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["storage_backend_kind"] == "local_fs"
    # Eager UC provisioning ran: catalog + default `main` schema both exist.
    assert "myws" in fake_uc.catalogs
    assert ("myws", "main") in fake_uc.schemas


async def test_create_workspace_rolls_back_on_uc_failure(
    auth_client: AsyncClient, backend: StorageBackend, fake_uc, db_session
):
    """If UC is unhealthy when the workspace is created, both the pg row
    and the owner membership are rolled back so the caller can retry."""
    from sqlalchemy import select

    from api.models.workspace import Workspace, WorkspaceMember

    fake_uc.fail_create_catalog = True
    resp = await auth_client.post(
        "/workspaces",
        json={"slug": "broken", "name": "Broken", "storage_backend_id": str(backend.id)},
    )
    assert resp.status_code == 502
    # Neither the workspace nor any membership for it lingers in pg.
    ws_rows = (await db_session.execute(select(Workspace).where(Workspace.slug == "broken"))).all()
    assert ws_rows == []
    mem_rows = (await db_session.execute(select(WorkspaceMember))).all()
    assert mem_rows == []


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


async def test_add_member_mirrors_uc_grant(
    auth_client: AsyncClient, backend: StorageBackend, db_session, fake_uc
):
    """Adding a member mirrors a best-effort catalog grant to UC (G-D10-a)."""
    from api.models.user import User
    from api.services.auth import hash_password

    target = User(
        email="grantee@test.local",
        password_hash=hash_password("pw"),
        name="Grantee",
        role="user",
    )
    db_session.add(target)
    await db_session.commit()
    await db_session.refresh(target)

    create = await auth_client.post(
        "/workspaces",
        json={"slug": "grant-ws", "name": "Grant WS", "storage_backend_id": str(backend.id)},
    )
    ws_slug = create.json()["slug"]
    resp = await auth_client.post(
        f"/workspaces/{ws_slug}/members",
        json={"user_id": str(target.id), "role": "writer"},
    )
    assert resp.status_code == 201

    grants = [c for c in fake_uc.permission_changes if c["principal"] == "grantee@test.local"]
    assert len(grants) == 1
    assert grants[0]["securable_type"] == "catalog"
    assert grants[0]["full_name"] == "grant-ws"
    assert grants[0]["add"] == ["SELECT", "MODIFY"]


async def test_add_member_survives_uc_grant_failure(
    auth_client: AsyncClient, backend: StorageBackend, db_session, fake_uc
):
    """A UC grant failure never blocks the membership change (best-effort)."""
    from api.models.user import User
    from api.services.auth import hash_password

    target = User(
        email="grantee2@test.local",
        password_hash=hash_password("pw"),
        name="G2",
        role="user",
    )
    db_session.add(target)
    await db_session.commit()
    await db_session.refresh(target)

    create = await auth_client.post(
        "/workspaces",
        json={"slug": "grant-ws2", "name": "Grant WS2", "storage_backend_id": str(backend.id)},
    )
    ws_slug = create.json()["slug"]
    fake_uc.fail_update_permissions = True

    resp = await auth_client.post(
        f"/workspaces/{ws_slug}/members",
        json={"user_id": str(target.id), "role": "reader"},
    )
    assert resp.status_code == 201
