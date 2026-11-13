import pytest
from httpx import AsyncClient
from sqlalchemy import select

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


async def test_explicit_null_clears_the_description(
    auth_client: AsyncClient, backend: StorageBackend
):
    create = await auth_client.post(
        "/workspaces",
        json={"slug": "clear-desc-ws", "name": "Ws", "storage_backend_id": str(backend.id)},
    )
    slug = create.json()["slug"]
    await auth_client.patch(f"/workspaces/{slug}", json={"description": "Something."})

    resp = await auth_client.patch(f"/workspaces/{slug}", json={"description": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] is None


async def test_omitting_description_leaves_it_unchanged(
    auth_client: AsyncClient, backend: StorageBackend
):
    create = await auth_client.post(
        "/workspaces",
        json={"slug": "keep-desc-ws", "name": "Ws", "storage_backend_id": str(backend.id)},
    )
    slug = create.json()["slug"]
    await auth_client.patch(f"/workspaces/{slug}", json={"description": "Keep me."})

    resp = await auth_client.patch(f"/workspaces/{slug}", json={"name": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "Keep me."


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


# --- default compute ---------------------------------------------------------


async def _plain_user(db_session, email: str):
    """A non-admin account: it holds no global agent permission, so its agent tiers
    come only from open mode and explicit grants."""
    from api.models.user import User
    from api.services.auth import hash_password

    u = User(email=email, password_hash=hash_password("pw"), name=email, role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _agent(db_session, name: str, *, access_mode: str = "open"):
    from api.models.agent import Agent

    a = Agent(name=name, status="healthy", access_mode=access_mode)
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


async def test_owner_sets_and_clears_the_default_compute(auth_client: AsyncClient, db_session):
    agent = await _agent(db_session, "shared")
    await auth_client.post("/workspaces", json={"slug": "dc-ws", "name": "Ws"})

    assert (await auth_client.get("/workspaces/dc-ws")).json()["default_agent"] is None

    resp = await auth_client.patch("/workspaces/dc-ws", json={"default_agent_id": str(agent.id)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_agent_id"] == str(agent.id)
    assert resp.json()["default_agent"]["name"] == "shared"
    # Not connected in this test, so presence reports it unavailable.
    assert resp.json()["default_agent"]["status"] == "unavailable"

    cleared = await auth_client.patch("/workspaces/dc-ws", json={"default_agent_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["default_agent_id"] is None
    assert cleared.json()["default_agent"] is None


async def test_omitting_the_default_compute_leaves_it_unchanged(
    auth_client: AsyncClient, db_session
):
    agent = await _agent(db_session, "keep-me")
    await auth_client.post("/workspaces", json={"slug": "dc-keep", "name": "Ws"})
    await auth_client.patch("/workspaces/dc-keep", json={"default_agent_id": str(agent.id)})

    resp = await auth_client.patch("/workspaces/dc-keep", json={"name": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_agent_id"] == str(agent.id)


async def test_an_unknown_default_compute_is_404(auth_client: AsyncClient):
    import uuid

    await auth_client.post("/workspaces", json={"slug": "dc-404", "name": "Ws"})
    resp = await auth_client.patch(
        "/workspaces/dc-404", json={"default_agent_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 404


async def test_only_an_owner_can_set_the_default_compute(auth_client: AsyncClient, db_session):
    agent = await _agent(db_session, "shared")
    writer = await _plain_user(db_session, "dc-writer@test.local")
    await auth_client.post("/workspaces", json={"slug": "dc-gate", "name": "Ws"})
    await auth_client.post(
        "/workspaces/dc-gate/members", json={"user_id": str(writer.id), "role": "writer"}
    )

    await _login(auth_client, writer.email)
    resp = await auth_client.patch("/workspaces/dc-gate", json={"default_agent_id": str(agent.id)})
    assert resp.status_code == 403


async def test_an_open_agent_needs_only_use_to_become_the_default(
    auth_client: AsyncClient, db_session
):
    agent = await _agent(db_session, "open-agent")  # open: everyone has `use`
    owner = await _plain_user(db_session, "dc-open-owner@test.local")

    await _login(auth_client, owner.email)
    await auth_client.post("/workspaces", json={"slug": "dc-open", "name": "Ws"})
    resp = await auth_client.patch("/workspaces/dc-open", json={"default_agent_id": str(agent.id)})
    assert resp.status_code == 200, resp.text


async def test_a_restricted_agent_needs_operate_to_become_the_default(
    auth_client: AsyncClient, db_session
):
    from api.models.agent_grant import AgentGrant

    agent = await _agent(db_session, "locked", access_mode="restricted")
    owner = await _plain_user(db_session, "dc-restricted-owner@test.local")
    await _login(auth_client, owner.email)
    await auth_client.post("/workspaces", json={"slug": "dc-restricted", "name": "Ws"})

    # No grant at all: the agent is invisible, so it answers 404 like a missing one.
    resp = await auth_client.patch(
        "/workspaces/dc-restricted", json={"default_agent_id": str(agent.id)}
    )
    assert resp.status_code == 404

    # `use` is enough to run on it, not to share it with a whole workspace.
    db_session.add(AgentGrant(agent_id=agent.id, user_id=owner.id, tier="use"))
    await db_session.commit()
    resp = await auth_client.patch(
        "/workspaces/dc-restricted", json={"default_agent_id": str(agent.id)}
    )
    assert resp.status_code == 403

    grant = (
        await db_session.execute(select(AgentGrant).where(AgentGrant.user_id == owner.id))
    ).scalar_one()
    grant.tier = "operate"
    await db_session.commit()
    resp = await auth_client.patch(
        "/workspaces/dc-restricted", json={"default_agent_id": str(agent.id)}
    )
    assert resp.status_code == 200, resp.text


async def test_members_see_the_default_compute_without_a_grant_on_it(
    auth_client: AsyncClient, db_session
):
    agent = await _agent(db_session, "locked-default", access_mode="restricted")
    reader = await _plain_user(db_session, "dc-reader@test.local")
    await auth_client.post("/workspaces", json={"slug": "dc-visible", "name": "Ws"})
    await auth_client.patch("/workspaces/dc-visible", json={"default_agent_id": str(agent.id)})
    await auth_client.post(
        "/workspaces/dc-visible/members", json={"user_id": str(reader.id), "role": "reader"}
    )

    await _login(auth_client, reader.email)
    resp = await auth_client.get("/workspaces/dc-visible")
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_agent"]["name"] == "locked-default"


async def test_resaving_an_unchanged_default_is_not_revalidated(
    auth_client: AsyncClient, db_session
):
    """An owner who lost access to the agent after setting it must still be able to
    save the page; only a *change* of agent is checked."""
    from api.models.agent_grant import AgentGrant

    agent = await _agent(db_session, "was-mine", access_mode="restricted")
    owner = await _plain_user(db_session, "dc-lost-access@test.local")
    db_session.add(AgentGrant(agent_id=agent.id, user_id=owner.id, tier="operate"))
    await db_session.commit()
    await _login(auth_client, owner.email)
    await auth_client.post("/workspaces", json={"slug": "dc-lost", "name": "Ws"})
    first = await auth_client.patch("/workspaces/dc-lost", json={"default_agent_id": str(agent.id)})
    assert first.status_code == 200, first.text

    await db_session.delete(
        (
            await db_session.execute(select(AgentGrant).where(AgentGrant.user_id == owner.id))
        ).scalar_one()
    )
    await db_session.commit()

    again = await auth_client.patch("/workspaces/dc-lost", json={"default_agent_id": str(agent.id)})
    assert again.status_code == 200, again.text
    assert again.json()["default_agent_id"] == str(agent.id)


async def test_a_new_workspace_keeps_time_in_utc(auth_client: AsyncClient):
    await auth_client.post("/workspaces", json={"slug": "tz-new", "name": "Ws"})

    assert (await auth_client.get("/workspaces/tz-new")).json()["timezone"] == "UTC"


async def test_owner_sets_the_time_zone(auth_client: AsyncClient):
    await auth_client.post("/workspaces", json={"slug": "tz-set", "name": "Ws"})

    resp = await auth_client.patch("/workspaces/tz-set", json={"timezone": "Europe/London"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "Europe/London"
    assert (await auth_client.get("/workspaces/tz-set")).json()["timezone"] == "Europe/London"


@pytest.mark.parametrize("timezone", ["Mars/Olympus_Mons", "", "europe/london", "+01:00", None])
async def test_a_time_zone_that_is_not_an_iana_name_is_refused(auth_client: AsyncClient, timezone):
    await auth_client.post("/workspaces", json={"slug": "tz-bad", "name": "Ws"})

    resp = await auth_client.patch("/workspaces/tz-bad", json={"timezone": timezone})

    assert resp.status_code == 422, resp.text
    assert (await auth_client.get("/workspaces/tz-bad")).json()["timezone"] == "UTC"
