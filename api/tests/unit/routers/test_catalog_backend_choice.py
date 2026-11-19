"""Naming a storage backend takes `storage:manage`, on every route that places data.

A catalog's members can read the ids of the backends its catalogs live on, so the
id alone must not be enough to put a new catalog, an export or a migration on
one. Leaving the backend out of a create still gives any owner bundled storage.
"""

from __future__ import annotations

import uuid

import pytest
from audit_helpers import audit_events
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import select

from api.models.catalog import KIND_DUCKLAKE, Catalog, WorkspaceCatalog
from api.models.catalog_export import CatalogExport
from api.models.catalog_migration import CatalogMigration
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services.auth import hash_password


async def _user(db_session, email: str, role: str = "user") -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=email, role=role)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _login(client: AsyncClient, user: User) -> None:
    resp = await client.post("/auth/login", json={"email": user.email, "password": "pw"})
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def admin(db_session) -> User:
    return await _user(db_session, "storage-admin@test.local", role="admin")


@pytest.fixture
async def prod(db_session, admin) -> StorageBackend:
    """An external backend an admin registered."""
    backend = StorageBackend(
        kind="s3", name="prod-lake", root_uri="s3://corp-prod-lake", config={}, created_by=admin.id
    )
    db_session.add(backend)
    await db_session.commit()
    await db_session.refresh(backend)
    return backend


@pytest.fixture
async def owner(db_session) -> User:
    """A regular account that owns a workspace."""
    owner = await _user(db_session, "owner@test.local")
    await seed_workspace(db_session, user_id=owner.id, slug="mine", name="Mine")
    return owner


async def _catalogs(db_session) -> set[str]:
    return set((await db_session.execute(select(Catalog.slug))).scalars())


@pytest.mark.parametrize("known", [True, False])
async def test_an_owner_cannot_create_a_catalog_on_a_named_backend(
    client, db_session, owner, prod, known
):
    await _login(client, owner)
    backend_id = prod.id if known else uuid.uuid4()

    resp = await client.post(
        "/workspaces/mine/catalogs", json={"name": "squat", "storage_backend_id": str(backend_id)}
    )

    # The same answer whether or not the id exists.
    assert resp.status_code == 403
    assert "storage:manage" in resp.json()["message"]
    assert "squat" not in await _catalogs(db_session)
    (denied,) = await audit_events(db_session, category="authz")
    assert denied.details == {"permission": "storage:manage"}


async def test_leaving_the_backend_out_still_gives_an_owner_bundled_storage(
    client, db_session, owner
):
    await _login(client, owner)

    resp = await client.post("/workspaces/mine/catalogs", json={"name": "mine_cat"})

    assert resp.status_code == 201, resp.text
    assert resp.json()["storage_backend_kind"] == "object_store"


async def test_storage_manage_can_create_a_catalog_on_a_named_backend(
    client, db_session, admin, prod
):
    await seed_workspace(db_session, user_id=admin.id, slug="ops", name="Ops")
    await _login(client, admin)

    resp = await client.post(
        "/workspaces/ops/catalogs", json={"name": "lake_cat", "storage_backend_id": str(prod.id)}
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["storage_backend_id"] == str(prod.id)


async def _ducklake_catalog(db_session, owner: User) -> Catalog:
    """A DuckLake catalog ``owner`` created, attached to their workspace."""
    ws, cat = await seed_workspace(
        db_session, user_id=owner.id, slug="lake-ws", name="Lake", catalog_slug="lake_src"
    )
    lake = Catalog(
        slug="lake",
        name="Lake",
        kind=KIND_DUCKLAKE,
        metadata_schema="cat_lake",
        storage_backend_id=cat.storage_backend_id,
        created_by=owner.id,
    )
    db_session.add(lake)
    await db_session.flush()
    db_session.add(WorkspaceCatalog(workspace_id=ws.id, catalog_id=lake.id, attached_by=owner.id))
    await db_session.commit()
    return lake


async def test_its_creator_cannot_export_a_catalog_to_a_named_backend(
    client, db_session, owner, prod
):
    lake = await _ducklake_catalog(db_session, owner)
    await _login(client, owner)

    resp = await client.post(
        f"/catalogs/{lake.id}/exports?workspace=lake-ws",
        json={"target_name": "berg", "target_storage_backend_id": str(prod.id)},
    )

    assert resp.status_code == 403
    assert "storage:manage" in resp.json()["message"]
    assert (await db_session.execute(select(CatalogExport))).scalars().all() == []


async def test_its_creator_cannot_migrate_a_catalog_to_a_named_backend(
    client, db_session, owner, prod
):
    _, catalog = await seed_workspace(
        db_session, user_id=owner.id, slug="dev", name="Dev", catalog_slug="dev_cat"
    )
    await _login(client, owner)

    resp = await client.post(
        f"/catalogs/{catalog.id}/migrations", json={"target_storage_backend_id": str(prod.id)}
    )

    assert resp.status_code == 403
    assert "storage:manage" in resp.json()["message"]
    assert (await db_session.execute(select(CatalogMigration))).scalars().all() == []
