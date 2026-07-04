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
    catalog_id = created.json()["id"]

    attach = await auth_client.post(
        "/workspaces/prod/catalogs/attach", json={"catalog_id": catalog_id}
    )
    assert attach.status_code == 200, attach.text
    assert attach.json()["attached_workspaces"] == 2

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
