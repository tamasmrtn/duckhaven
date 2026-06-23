"""Unit tests for the built-in system catalog: provisioning, auto-attach, and
the read-only/built-in guards."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from conftest import seed_workspace
from fake_polaris import FakePolaris
from fastapi import HTTPException

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services import catalog as catalog_service
from api.services.auth import hash_password
from api.services.system_catalog.bootstrap import (
    ensure_system_catalog,
    get_system_catalog,
    link_system_catalog,
    provision_system_catalog,
)
from api.services.system_catalog.constants import SYSTEM_CATALOG_SLUG
from api.services.workspace import validate_catalog_slug


@pytest.fixture
async def admin(db_session) -> User:
    u = User(email="a@test.local", password_hash=hash_password("pw"), name="A", role="admin")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


def _backend(created_by) -> StorageBackend:
    return StorageBackend(kind="object_store", name="System", root_uri="", created_by=created_by)


async def test_provision_is_idempotent(db_session, fake_polaris: FakePolaris, admin):
    first = await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )
    second = await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )
    assert first.id == second.id
    assert first.is_system is True
    count = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Catalog).where(Catalog.is_system.is_(True))
    )
    assert count == 1


async def test_provision_backfills_existing_workspaces(db_session, fake_polaris, admin):
    ws1, _ = await seed_workspace(db_session, user_id=admin.id, slug="ws1", name="One")
    ws2, _ = await seed_workspace(db_session, user_id=admin.id, slug="ws2", name="Two")

    catalog = await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )

    for ws in (ws1, ws2):
        link = (
            await db_session.execute(
                sa.select(WorkspaceCatalog).where(
                    WorkspaceCatalog.workspace_id == ws.id,
                    WorkspaceCatalog.catalog_id == catalog.id,
                )
            )
        ).scalar_one()
        # System catalog is attached but never the workspace default.
        assert link.is_default is False


async def test_link_system_catalog_noop_before_provision(db_session, admin):
    ws, _ = await seed_workspace(db_session, user_id=admin.id, slug="ws", name="WS")
    # No system catalog provisioned yet → link is a no-op (no error).
    await link_system_catalog(db_session, ws.id)
    await db_session.commit()
    assert await get_system_catalog(db_session) is None


async def test_link_system_catalog_after_provision(db_session, fake_polaris, admin):
    catalog = await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )
    ws, _ = await seed_workspace(db_session, user_id=admin.id, slug="late", name="Late")
    # seed_workspace doesn't link system; do it explicitly (the create_workspace
    # route does this), and confirm idempotency.
    await link_system_catalog(db_session, ws.id)
    await link_system_catalog(db_session, ws.id)
    await db_session.commit()
    links = (
        (
            await db_session.execute(
                sa.select(WorkspaceCatalog).where(
                    WorkspaceCatalog.workspace_id == ws.id,
                    WorkspaceCatalog.catalog_id == catalog.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(links) == 1


async def test_ensure_system_catalog_noop_when_absent(db_session, fake_polaris):
    assert await ensure_system_catalog(db_session, fake_polaris) is None


async def test_ensure_system_catalog_self_heals_links(db_session, fake_polaris, admin):
    await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )
    # A workspace created without a link (simulating a gap) gets healed.
    ws, _ = await seed_workspace(db_session, user_id=admin.id, slug="gap", name="Gap")
    await ensure_system_catalog(db_session, fake_polaris)
    catalog = await get_system_catalog(db_session)
    link = (
        await db_session.execute(
            sa.select(WorkspaceCatalog).where(
                WorkspaceCatalog.workspace_id == ws.id,
                WorkspaceCatalog.catalog_id == catalog.id,
            )
        )
    ).scalar_one_or_none()
    assert link is not None


async def test_detach_system_catalog_rejected(db_session, fake_polaris, admin):
    catalog = await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )
    ws, _ = await seed_workspace(db_session, user_id=admin.id, slug="ws", name="WS")
    with pytest.raises(HTTPException) as exc:
        await catalog_service.detach_catalog(db_session, workspace=ws, catalog=catalog)
    assert exc.value.status_code == 409


async def test_drop_system_catalog_rejected(db_session, fake_polaris, admin):
    catalog = await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )
    with pytest.raises(HTTPException) as exc:
        await catalog_service.drop_catalog(db_session, fake_polaris, catalog=catalog)
    assert exc.value.status_code == 409


async def test_system_slug_is_reserved():
    with pytest.raises(HTTPException) as exc:
        validate_catalog_slug(SYSTEM_CATALOG_SLUG)
    assert exc.value.status_code == 422


async def test_list_attachable_excludes_system(db_session, fake_polaris, admin):
    await seed_workspace(
        db_session, user_id=admin.id, slug="ws", name="WS", catalog_slug="user_cat"
    )
    await provision_system_catalog(
        db_session, fake_polaris, backend=_backend(admin.id), created_by=admin.id
    )
    attachable = {c.slug for c in await catalog_service.list_attachable(db_session)}
    assert "user_cat" in attachable
    assert SYSTEM_CATALOG_SLUG not in attachable
