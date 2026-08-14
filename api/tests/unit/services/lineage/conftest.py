"""Shared seeding for the DB-backed lineage tests."""

from __future__ import annotations

import uuid

import pytest_asyncio

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember


@pytest_asyncio.fixture
async def graph_env(db_session):
    """A workspace with two attached catalogs (`warehouse` and `raw`) and an owner.

    Two catalogs rather than one because the interesting lineage cases — and the
    workspace-boundary cases — are cross-catalog.
    """
    user = User(email=f"owner-{uuid.uuid4().hex[:8]}@example.com", name="Owner", role="user")
    db_session.add(user)
    await db_session.flush()

    backend = StorageBackend(
        kind="object_store", name="store", root_uri="/tmp/test", created_by=user.id
    )
    db_session.add(backend)
    await db_session.flush()

    ws = Workspace(slug="lineage-ws", name="Lineage WS")
    db_session.add(ws)
    await db_session.flush()

    catalogs = {}
    for index, slug in enumerate(("warehouse", "raw")):
        catalog = Catalog(
            slug=slug,
            name=slug,
            polaris_name=f"pol_{slug}",
            storage_backend_id=backend.id,
            created_by=user.id,
        )
        db_session.add(catalog)
        await db_session.flush()
        db_session.add(
            WorkspaceCatalog(
                workspace_id=ws.id,
                catalog_id=catalog.id,
                is_default=(index == 0),
                attached_by=user.id,
            )
        )
        catalogs[slug] = catalog

    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await db_session.commit()
    return {"db": db_session, "user": user, "workspace": ws, "catalogs": catalogs}
