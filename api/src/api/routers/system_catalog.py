"""The ``duckhaven`` system catalog's tables, for the catalog tree and autocomplete.

The catalog is never one of the workspace's catalogs, so the catalog and schema
routes cannot list it. Its tables are fixed, so they are listed from their
definitions rather than read through an agent.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_db, require_permission
from api.models.user import User
from api.schemas.system_catalog import SystemCatalogOut, SystemColumnOut, SystemTableOut
from api.services.permissions import Permission
from api.services.system_catalog import attach, store
from api.services.system_catalog.authorize import shadowed
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)
from duckhaven_shared.system_catalog import SYSTEM_CATALOG_ALIAS

router = APIRouter()

_DESCRIPTIONS = {
    "audit": "Every event of the access audit log",
    "grants": "Current access, one row per grant of any kind",
    "history": "Finished queries",
}


@router.get(
    "/workspaces/{workspace}/system-catalog",
    response_model=SystemCatalogOut,
    # Both, as a query on it needs; each refusal is recorded like any other.
    dependencies=[Depends(require_permission(Permission.AUDIT_READ))],
)
async def get_system_catalog(
    ws: Annotated[str, Path(alias="workspace")],
    user: User = Depends(require_permission(Permission.QUERIES_ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> SystemCatalogOut:
    """The ``duckhaven`` system catalog's tables and their columns.

    Needs `audit:read` and `queries:admin`, as reading it does. 404 where this
    workspace cannot read it: the system catalog is turned off, or the workspace
    has a catalog of its own named `duckhaven`. `available` is false while its
    database cannot be reached, when a query on it fails."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    if not settings.system_catalog_enabled or shadowed(
        await resolve_workspace_catalogs(db, workspace.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return SystemCatalogOut(
        catalog=SYSTEM_CATALOG_ALIAS,
        available=attach.is_available(),
        tables=[
            SystemTableOut(
                schema_name=table.schema,
                name=table.name,
                description=_DESCRIPTIONS[table.name],
                columns=[
                    SystemColumnOut(name=c.name, type=store.duckdb_type(c)) for c in table.columns
                ],
            )
            for table in store.READABLE
        ],
    )
