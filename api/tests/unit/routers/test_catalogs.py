"""Unit tests for catalog lifecycle endpoints (decoupled M:N catalogs)."""

from __future__ import annotations

import pytest
from conftest import seed_workspace
from fake_polaris import FakePolaris
from httpx import AsyncClient

from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="cat@test.local", password_hash=hash_password("pw"), name="Cat", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "cat@test.local", "password": "pw"})
    return client


async def test_create_catalog_and_list(auth_client, owner, db_session, fake_polaris: FakePolaris):
    await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")

    resp = await auth_client.post("/workspaces/dev/catalogs", json={"name": "curated"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["slug"] == "curated"
    # The new catalog was provisioned in Polaris under its own name.
    assert "curated" in fake_polaris.catalogs

    listing = await auth_client.get("/workspaces/dev/catalogs")
    assert listing.status_code == 200
    by_slug = {c["slug"]: c for c in listing.json()}
    assert set(by_slug) == {"dev", "curated"}
    # The seeded catalog is the default; the new one is not.
    assert by_slug["dev"]["is_default"] is True
    assert by_slug["curated"]["is_default"] is False
    # The listing carries the storage backend's kind, name and root URI so the
    # UI can show an indicator + catalog-information panel without an admin call.
    assert by_slug["curated"]["storage_backend_kind"] == "object_store"
    assert "storage_backend_name" in by_slug["curated"]
    assert "storage_backend_root_uri" in by_slug["curated"]
    # Attachments default to open access; the listing surfaces the mode so the
    # admin toggle and tree badge can read it without a per-catalog grants call.
    assert by_slug["curated"]["access_mode"] == "open"


async def test_catalog_list_reflects_scoped_access_mode(
    auth_client, owner, db_session, fake_polaris: FakePolaris
):
    await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    await auth_client.patch(
        "/workspaces/dev/catalogs/dev/access-mode", json={"access_mode": "scoped"}
    )

    by_slug = {c["slug"]: c for c in (await auth_client.get("/workspaces/dev/catalogs")).json()}
    assert by_slug["dev"]["access_mode"] == "scoped"


async def test_create_requires_owner(auth_client, owner, db_session):
    await seed_workspace(db_session, user_id=owner.id, slug="ro", name="RO", role="reader")
    resp = await auth_client.post("/workspaces/ro/catalogs", json={"name": "c"})
    assert resp.status_code == 403


async def test_invalid_slug_rejected(auth_client, owner, db_session):
    await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    resp = await auth_client.post("/workspaces/dev/catalogs", json={"name": "Bad-Slug"})
    assert resp.status_code == 422


async def test_attach_same_catalog_to_two_workspaces(auth_client, owner, db_session, fake_polaris):
    """A catalog created in one workspace can be attached to another (M:N)."""
    await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    await seed_workspace(db_session, user_id=owner.id, slug="prod", name="Prod")

    created = await auth_client.post("/workspaces/dev/catalogs", json={"name": "shared"})
    catalog = created.json()["slug"]

    # A PUT on the attachment's own address: 201 the first time it is created.
    attach = await auth_client.put(f"/workspaces/prod/catalogs/{catalog}", json={})
    assert attach.status_code == 201, attach.text
    assert attach.json()["attached_workspaces"] == 2

    # Idempotent: attaching again updates rather than conflicting.
    again = await auth_client.put(f"/workspaces/prod/catalogs/{catalog}", json={})
    assert again.status_code == 200, again.text

    prod_catalogs = {c["slug"] for c in (await auth_client.get("/workspaces/prod/catalogs")).json()}
    assert "shared" in prod_catalogs


async def test_drop_blocked_while_attached_then_succeeds(
    auth_client, owner, db_session, fake_polaris
):
    await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    created = await auth_client.post("/workspaces/dev/catalogs", json={"name": "temp"})
    catalog_id = created.json()["id"]

    # Still attached to 'dev' → drop is refused.
    blocked = await auth_client.delete(f"/catalogs/{catalog_id}")
    assert blocked.status_code == 409

    detach = await auth_client.delete("/workspaces/dev/catalogs/temp")
    assert detach.status_code == 204

    dropped = await auth_client.delete(f"/catalogs/{catalog_id}")
    assert dropped.status_code == 204
    assert "temp" not in fake_polaris.catalogs


async def test_detach_default_promotes_another(auth_client, owner, db_session, fake_polaris):
    await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    await auth_client.post("/workspaces/dev/catalogs", json={"name": "second"})

    # Detach the default ('dev'); the remaining catalog must become default.
    detach = await auth_client.delete("/workspaces/dev/catalogs/dev")
    assert detach.status_code == 204

    listing = {c["slug"]: c for c in (await auth_client.get("/workspaces/dev/catalogs")).json()}
    assert set(listing) == {"second"}
    assert listing["second"]["is_default"] is True


# --- access mode at creation --------------------------------------------------


async def test_create_catalog_defaults_to_open(
    auth_client, owner, db_session, fake_polaris: FakePolaris
):
    await seed_workspace(db_session, user_id=owner.id, slug="dm", name="DM")

    resp = await auth_client.post("/workspaces/dm/catalogs", json={"name": "plain"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["access_mode"] == "open"


async def test_create_catalog_scoped_sets_the_attachment_mode(
    auth_client, owner, db_session, fake_polaris: FakePolaris
):
    """Chosen at creation so a catalog meant to be scoped is never readable by
    every workspace member in the window before someone switches it."""
    await seed_workspace(db_session, user_id=owner.id, slug="sc", name="SC")

    resp = await auth_client.post(
        "/workspaces/sc/catalogs", json={"name": "sensitive", "access_mode": "scoped"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["access_mode"] == "scoped"


async def test_creating_scoped_seeds_the_creator_a_catalog_grant(
    auth_client, owner, db_session, fake_polaris: FakePolaris
):
    """A scoped catalog has no bypass: `grants.access_tier` returns None without a
    covering grant whatever the workspace role. Without this seed the creator
    would make a catalog nobody -- including themselves -- could see."""
    from sqlalchemy import select

    from api.models.catalog import Catalog
    from api.models.catalog_grant import CatalogGrant

    await seed_workspace(db_session, user_id=owner.id, slug="seed", name="Seed")
    resp = await auth_client.post(
        "/workspaces/seed/catalogs", json={"name": "locked", "access_mode": "scoped"}
    )
    assert resp.status_code == 201, resp.text

    catalog = (
        await db_session.execute(select(Catalog).where(Catalog.slug == "locked"))
    ).scalar_one()
    grant = (
        await db_session.execute(select(CatalogGrant).where(CatalogGrant.catalog_id == catalog.id))
    ).scalar_one()
    assert grant.user_id == owner.id
    assert grant.tier == "writer"
    # Catalog-level: covers every schema and table, including future ones.
    assert grant.schema_name is None
    assert grant.table_name is None


async def test_creating_open_seeds_no_grant(
    auth_client, owner, db_session, fake_polaris: FakePolaris
):
    """Grants are only consulted in scoped mode, so an open catalog must not
    accumulate rows nobody reads."""
    from sqlalchemy import func, select

    from api.models.catalog_grant import CatalogGrant

    await seed_workspace(db_session, user_id=owner.id, slug="og", name="OG")
    await auth_client.post("/workspaces/og/catalogs", json={"name": "shared"})

    count = await db_session.scalar(select(func.count()).select_from(CatalogGrant))
    assert count == 0


async def test_create_catalog_rejects_unknown_access_mode(
    auth_client, owner, db_session, fake_polaris: FakePolaris
):
    await seed_workspace(db_session, user_id=owner.id, slug="bad", name="Bad")
    resp = await auth_client.post(
        "/workspaces/bad/catalogs", json={"name": "nope", "access_mode": "restricted"}
    )
    assert resp.status_code == 422


async def test_attach_is_idempotent_and_can_move_the_default(
    auth_client, owner, db_session, fake_polaris
):
    """The attach route is a PUT, so repeating it is a no-op rather than the 409
    the old POST raised — and the one field the body carries still takes effect
    on the repeat."""
    await seed_workspace(db_session, user_id=owner.id, slug="ws1", name="WS1")
    await seed_workspace(db_session, user_id=owner.id, slug="ws2", name="WS2")
    catalog = (await auth_client.post("/workspaces/ws1/catalogs", json={"name": "shared"})).json()[
        "slug"
    ]

    created = await auth_client.put(f"/workspaces/ws2/catalogs/{catalog}", json={})
    assert created.status_code == 201, created.text

    repeated = await auth_client.put(f"/workspaces/ws2/catalogs/{catalog}", json={})
    assert repeated.status_code == 200, repeated.text

    # The repeat still applies the body: `shared` was not ws2's default before.
    moved = await auth_client.put(
        f"/workspaces/ws2/catalogs/{catalog}", json={"make_default": True}
    )
    assert moved.status_code == 200, moved.text
    listing = {
        c["slug"]: c["is_default"]
        for c in (await auth_client.get("/workspaces/ws2/catalogs")).json()
    }
    assert listing[catalog] is True
    assert sum(1 for v in listing.values() if v) == 1
