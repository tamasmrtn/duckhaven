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
        kind="object_store",
        name="primary",
        root_uri="",
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


async def test_create_and_list_workspace(auth_client: AsyncClient, fake_polaris):
    resp = await auth_client.post("/workspaces", json={"slug": "myws", "name": "My WS"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "myws"
    # A new workspace starts with no catalog, so no default storage to surface.
    assert data["default_catalog"] is None
    assert data["storage_backend_kind"] is None

    list_resp = await auth_client.get("/workspaces")
    listed = list_resp.json()
    assert len(listed) == 1
    assert listed[0]["default_catalog"] is None

    detail_resp = await auth_client.get("/workspaces/myws")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["default_catalog"] is None


async def test_create_workspace_creates_no_catalog(
    auth_client: AsyncClient, fake_polaris, db_session
):
    """Workspace creation provisions nothing else: no catalog, no storage
    backend, and no Polaris call. Catalogs are created/attached afterward."""
    from sqlalchemy import select

    from api.models.catalog import Catalog, WorkspaceCatalog

    resp = await auth_client.post("/workspaces", json={"slug": "empty", "name": "Empty"})
    assert resp.status_code == 201

    assert (await db_session.execute(select(Catalog))).all() == []
    assert (await db_session.execute(select(WorkspaceCatalog))).all() == []
    assert (await db_session.execute(select(StorageBackend))).all() == []
    assert fake_polaris.catalogs == {}

    # Browsing schemas before any catalog exists yields a clean 404, not a 500.
    schemas = await auth_client.get("/workspaces/empty/schemas")
    assert schemas.status_code == 404


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


async def test_add_member_succeeds(
    auth_client: AsyncClient, backend: StorageBackend, db_session, fake_polaris
):
    """Adding a member succeeds. The catalog grant mirror is a no-op now —
    DuckHaven enforces membership at the API boundary (D10)."""
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
    assert resp.json()["role"] == "writer"


# --- update/delete (Settings > Workspace) ---


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/auth/login", json={"email": email, "password": "pw"})


async def test_owner_can_rename_and_describe_workspace(
    auth_client: AsyncClient, backend: StorageBackend
):
    create = await auth_client.post(
        "/workspaces",
        json={"slug": "rename-ws", "name": "Old Name", "storage_backend_id": str(backend.id)},
    )
    slug = create.json()["slug"]

    resp = await auth_client.patch(
        f"/workspaces/{slug}",
        json={"name": "New Name", "description": "What this workspace is for."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Name"
    assert resp.json()["description"] == "What this workspace is for."
    assert resp.json()["slug"] == "rename-ws"  # slug is not renameable here

    detail = await auth_client.get(f"/workspaces/{slug}")
    assert detail.json()["name"] == "New Name"


async def test_writer_cannot_rename_workspace(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    from api.models.user import User
    from api.services.auth import hash_password

    writer = User(
        email="writer@test.local", password_hash=hash_password("pw"), name="Writer", role="user"
    )
    db_session.add(writer)
    await db_session.commit()
    await db_session.refresh(writer)

    create = await auth_client.post(
        "/workspaces",
        json={"slug": "gate-ws", "name": "Gate WS", "storage_backend_id": str(backend.id)},
    )
    slug = create.json()["slug"]
    await auth_client.post(
        f"/workspaces/{slug}/members", json={"user_id": str(writer.id), "role": "writer"}
    )

    await _login(auth_client, writer.email)
    resp = await auth_client.patch(f"/workspaces/{slug}", json={"name": "Hijacked"})
    assert resp.status_code == 403


async def test_owner_can_delete_workspace_and_dependents_are_gone(
    auth_client: AsyncClient, backend: StorageBackend, db_session, fake_polaris
):
    from sqlalchemy import select

    from api.models.catalog import Catalog, WorkspaceCatalog
    from api.models.query import SavedQuery
    from api.models.workspace import Workspace

    create = await auth_client.post(
        "/workspaces",
        json={"slug": "delete-ws", "name": "Delete WS", "storage_backend_id": str(backend.id)},
    )
    slug = create.json()["slug"]
    cat_resp = await auth_client.post(
        f"/workspaces/{slug}/catalogs",
        json={"name": "delete_ws_cat", "storage_backend_id": str(backend.id)},
    )
    assert cat_resp.status_code == 201, cat_resp.text
    saved = await auth_client.post(
        f"/workspaces/{slug}/saved-queries", json={"name": "keep-me-gone", "sql": "SELECT 1"}
    )
    assert saved.status_code == 201, saved.text

    ws_row = (
        await db_session.execute(select(Workspace).where(Workspace.slug == slug))
    ).scalar_one()
    catalog_id = (
        (
            await db_session.execute(
                select(WorkspaceCatalog.catalog_id).where(
                    WorkspaceCatalog.workspace_id == ws_row.id
                )
            )
        )
        .scalars()
        .first()
    )
    assert catalog_id is not None

    resp = await auth_client.delete(f"/workspaces/{slug}")
    assert resp.status_code == 204

    assert (await auth_client.get(f"/workspaces/{slug}")).status_code == 404
    assert (
        await db_session.execute(select(SavedQuery).where(SavedQuery.workspace_id == ws_row.id))
    ).first() is None
    # The attached catalog survives — it's decoupled M:N, not owned by the workspace.
    survives = (
        await db_session.execute(select(Catalog).where(Catalog.id == catalog_id))
    ).scalar_one_or_none()
    assert survives is not None


async def test_non_owner_cannot_delete_workspace(
    auth_client: AsyncClient, backend: StorageBackend, db_session
):
    from api.models.user import User
    from api.services.auth import hash_password

    reader = User(
        email="reader@test.local", password_hash=hash_password("pw"), name="Reader", role="user"
    )
    db_session.add(reader)
    await db_session.commit()
    await db_session.refresh(reader)

    create = await auth_client.post(
        "/workspaces",
        json={
            "slug": "protected-ws",
            "name": "Protected WS",
            "storage_backend_id": str(backend.id),
        },
    )
    slug = create.json()["slug"]
    await auth_client.post(
        f"/workspaces/{slug}/members", json={"user_id": str(reader.id), "role": "reader"}
    )

    await _login(auth_client, reader.email)
    resp = await auth_client.delete(f"/workspaces/{slug}")
    assert resp.status_code == 403
