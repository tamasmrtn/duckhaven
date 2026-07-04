"""Grants management API: access-mode toggle + grant CRUD (issue #129)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import hash_password


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="owner@g.local", password_hash=hash_password("pw"), name="Owner", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@g.local", "password": "pw"})
    return client


async def _make(auth_client, owner, db_session):
    """Owner-created workspace + catalog + one extra member. Returns
    (slug, catalog_slug, workspace, catalog, member)."""
    resp = await auth_client.post("/workspaces", json={"slug": "alpha", "name": "Alpha"})
    slug = resp.json()["slug"]
    sb = StorageBackend(kind="object_store", name="p", root_uri="", created_by=owner.id)
    db_session.add(sb)
    await db_session.commit()
    await db_session.refresh(sb)
    cat_resp = await auth_client.post(
        f"/workspaces/{slug}/catalogs", json={"name": "alpha", "storage_backend_id": str(sb.id)}
    )
    catalog_slug = cat_resp.json()["slug"]

    ws = (await db_session.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one()
    cat = (
        await db_session.execute(select(Catalog).where(Catalog.slug == catalog_slug))
    ).scalar_one()
    member = User(email="m@g.local", password_hash=hash_password("pw"), name="Member", role="user")
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(member)
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id, role="reader"))
    await db_session.commit()
    return slug, catalog_slug, ws, cat, member


async def test_toggle_access_mode(auth_client, owner, db_session):
    slug, cslug, ws, cat, member = await _make(auth_client, owner, db_session)
    r = await auth_client.patch(
        f"/workspaces/{slug}/catalogs/{cslug}/access-mode", json={"access_mode": "scoped"}
    )
    assert r.status_code == 200
    assert r.json()["access_mode"] == "scoped"

    mode = (
        await db_session.execute(
            select(WorkspaceCatalog.access_mode).where(WorkspaceCatalog.catalog_id == cat.id)
        )
    ).scalar_one()
    assert mode == "scoped"


async def test_upsert_and_list_grant(auth_client, owner, db_session):
    slug, cslug, ws, cat, member = await _make(auth_client, owner, db_session)
    r = await auth_client.put(
        f"/workspaces/{slug}/catalogs/{cslug}/grants",
        json={"user_id": str(member.id), "schema_name": "marketing", "tier": "reader"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["tier"] == "reader"
    assert r.json()["user_name"] == "Member"

    # Idempotent upsert: same node updates in place (200, not a duplicate).
    r = await auth_client.put(
        f"/workspaces/{slug}/catalogs/{cslug}/grants",
        json={"user_id": str(member.id), "schema_name": "marketing", "tier": "writer"},
    )
    assert r.status_code == 200
    assert r.json()["tier"] == "writer"

    listing = await auth_client.get(f"/workspaces/{slug}/catalogs/{cslug}/grants")
    body = listing.json()
    assert len(body["grants"]) == 1
    assert {p["name"] for p in body["principals"]} == {"Owner", "Member"}


async def test_grant_rejects_non_member(auth_client, owner, db_session):
    slug, cslug, ws, cat, member = await _make(auth_client, owner, db_session)
    stranger = User(email="x@g.local", password_hash=hash_password("pw"), name="X", role="user")
    db_session.add(stranger)
    await db_session.commit()
    await db_session.refresh(stranger)
    r = await auth_client.put(
        f"/workspaces/{slug}/catalogs/{cslug}/grants",
        json={"user_id": str(stranger.id), "tier": "reader"},
    )
    assert r.status_code == 422


async def test_table_grant_requires_schema(auth_client, owner, db_session):
    slug, cslug, ws, cat, member = await _make(auth_client, owner, db_session)
    r = await auth_client.put(
        f"/workspaces/{slug}/catalogs/{cslug}/grants",
        json={"user_id": str(member.id), "table_name": "leads", "tier": "reader"},
    )
    assert r.status_code == 422


async def test_delete_grant(auth_client, owner, db_session):
    slug, cslug, ws, cat, member = await _make(auth_client, owner, db_session)
    r = await auth_client.put(
        f"/workspaces/{slug}/catalogs/{cslug}/grants",
        json={"user_id": str(member.id), "tier": "metadata"},
    )
    grant_id = r.json()["id"]
    d = await auth_client.delete(f"/workspaces/{slug}/catalogs/{cslug}/grants/{grant_id}")
    assert d.status_code == 204
    remaining = (
        (await db_session.execute(select(CatalogGrant).where(CatalogGrant.catalog_id == cat.id)))
        .scalars()
        .all()
    )
    assert remaining == []


async def test_non_owner_forbidden(client, db_session, owner):
    # Set up as owner, then act as the plain reader member.
    await client.post("/auth/login", json={"email": "owner@g.local", "password": "pw"})
    slug, cslug, ws, cat, member = await _make(client, owner, db_session)
    await client.post("/auth/login", json={"email": "m@g.local", "password": "pw"})
    r = await client.get(f"/workspaces/{slug}/catalogs/{cslug}/grants")
    assert r.status_code == 403
