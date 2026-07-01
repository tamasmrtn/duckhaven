"""Unit tests for the catalog storage-backend migration endpoints + freeze gate."""

from __future__ import annotations

import uuid

import pytest
from conftest import seed_workspace
from httpx import AsyncClient

from api.models.catalog_migration import CatalogMigration, CatalogMigrationEvent
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services.auth import hash_password


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="mig@test.local", password_hash=hash_password("pw"), name="Mig", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "mig@test.local", "password": "pw"})
    return client


async def _target_backend(db_session, owner) -> StorageBackend:
    backend = StorageBackend(
        kind="object_store", name="target", root_uri="/tmp/target", created_by=owner.id
    )
    db_session.add(backend)
    await db_session.commit()
    await db_session.refresh(backend)
    return backend


async def test_start_migration(auth_client, owner, db_session):
    _, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    target = await _target_backend(db_session, owner)

    resp = await auth_client.post(
        f"/catalogs/{catalog.id}/migrations",
        json={"target_storage_backend_id": str(target.id)},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["target_storage_backend_id"] == str(target.id)


async def test_start_same_backend_rejected(auth_client, owner, db_session):
    _, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    resp = await auth_client.post(
        f"/catalogs/{catalog.id}/migrations",
        json={"target_storage_backend_id": str(catalog.storage_backend_id)},
    )
    assert resp.status_code == 422


async def test_start_unknown_target(auth_client, owner, db_session):
    _, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    resp = await auth_client.post(
        f"/catalogs/{catalog.id}/migrations",
        json={"target_storage_backend_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_second_concurrent_migration_conflicts(auth_client, owner, db_session):
    _, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    target = await _target_backend(db_session, owner)
    first = await auth_client.post(
        f"/catalogs/{catalog.id}/migrations",
        json={"target_storage_backend_id": str(target.id)},
    )
    assert first.status_code == 202
    second = await auth_client.post(
        f"/catalogs/{catalog.id}/migrations",
        json={"target_storage_backend_id": str(target.id)},
    )
    assert second.status_code == 409


async def test_start_requires_creator_or_admin(client, owner, db_session):
    _, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    target = await _target_backend(db_session, owner)
    other = User(email="other@test.local", password_hash=hash_password("pw"), name="O", role="user")
    db_session.add(other)
    await db_session.commit()
    await client.post("/auth/login", json={"email": "other@test.local", "password": "pw"})

    resp = await client.post(
        f"/catalogs/{catalog.id}/migrations",
        json={"target_storage_backend_id": str(target.id)},
    )
    assert resp.status_code == 403


async def test_status_list_and_logs(auth_client, owner, db_session):
    _, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    migration = CatalogMigration(
        catalog_id=catalog.id,
        source_storage_backend_id=catalog.storage_backend_id,
        target_storage_backend_id=catalog.storage_backend_id,
        created_by=owner.id,
        status="copying",
        tables_total=3,
        tables_done=1,
    )
    db_session.add(migration)
    await db_session.flush()
    for i, msg in enumerate(("started", "copied a", "copied b"), start=1):
        db_session.add(
            CatalogMigrationEvent(migration_id=migration.id, seq=i, level="info", message=msg)
        )
    await db_session.commit()

    listing = await auth_client.get(f"/catalogs/{catalog.id}/migrations")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == str(migration.id)

    detail = await auth_client.get(f"/catalogs/{catalog.id}/migrations/{migration.id}")
    assert detail.status_code == 200
    assert detail.json()["tables_done"] == 1

    logs = await auth_client.get(f"/catalogs/{catalog.id}/migrations/{migration.id}/logs")
    assert [e["message"] for e in logs.json()] == ["started", "copied a", "copied b"]
    # Incremental cursor returns only newer events.
    tail = await auth_client.get(
        f"/catalogs/{catalog.id}/migrations/{migration.id}/logs", params={"after": 2}
    )
    assert [e["seq"] for e in tail.json()] == [3]


async def test_cancel_migration(auth_client, owner, db_session):
    _, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    migration = CatalogMigration(
        catalog_id=catalog.id,
        source_storage_backend_id=catalog.storage_backend_id,
        target_storage_backend_id=catalog.storage_backend_id,
        created_by=owner.id,
        status="copying",
    )
    db_session.add(migration)
    await db_session.commit()

    resp = await auth_client.post(f"/catalogs/{catalog.id}/migrations/{migration.id}/cancel")
    assert resp.status_code == 200
    await db_session.refresh(migration)
    assert migration.cancel_requested is True


async def test_freeze_gate_blocks_writes_allows_reads(auth_client, owner, db_session):
    ws, catalog = await seed_workspace(db_session, user_id=owner.id, slug="dev", name="Dev")
    db_session.add(
        CatalogMigration(
            catalog_id=catalog.id,
            source_storage_backend_id=catalog.storage_backend_id,
            target_storage_backend_id=catalog.storage_backend_id,
            created_by=owner.id,
            status="copying",
        )
    )
    await db_session.commit()

    write = await auth_client.post(
        "/workspaces/dev/queries",
        json={"sql": "INSERT INTO analytics.t VALUES (1)", "agent_id": str(uuid.uuid4())},
    )
    assert write.status_code == 409
    assert write.json()["detail"]["error"] == "catalog_read_only"

    # A read is not blocked by the gate (it fails later on the missing agent).
    read = await auth_client.post(
        "/workspaces/dev/queries",
        json={"sql": "SELECT 1", "agent_id": str(uuid.uuid4())},
    )
    assert read.status_code != 409
