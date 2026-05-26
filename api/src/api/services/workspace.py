import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.workspace import Workspace, WorkspaceMember
from api.services.unity_catalog import UCClient, UCConflictError

logger = logging.getLogger(__name__)

ROLE_ORDER = {"reader": 0, "writer": 1, "owner": 2}

# Workspace role → UC catalog privileges. DuckHaven is the sole permission
# authority (D10); these grants are defense-in-depth only.
ROLE_PRIVILEGES = {
    "reader": ["SELECT"],
    "writer": ["SELECT", "MODIFY"],
    "owner": ["ALL PRIVILEGES"],
}


async def mirror_member_grant(uc: UCClient, catalog: str, principal: str, role: str) -> None:
    """Best-effort mirror of a workspace membership to a UC catalog grant
    (G-D10-a). Any UC failure is logged and swallowed so a membership change
    is never blocked by UC availability or missing grant support."""
    privileges = ROLE_PRIVILEGES.get(role, ["SELECT"])
    try:
        await uc.update_permissions("catalog", catalog, principal=principal, add=privileges)
    except Exception as exc:  # noqa: BLE001 - best-effort; never blocks the change
        logger.warning("UC grant mirror failed for %s on %s: %s", principal, catalog, exc)


async def assert_workspace_member(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    min_role: str = "reader",
) -> WorkspaceMember:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if ROLE_ORDER.get(member.role, -1) < ROLE_ORDER.get(min_role, 0):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return member


async def get_workspace(db: AsyncSession, slug_or_id: str) -> Workspace | None:
    stmt = select(Workspace).options(selectinload(Workspace.storage_backend))
    try:
        ws_id = uuid.UUID(slug_or_id)
        result = await db.execute(stmt.where(Workspace.id == ws_id))
    except ValueError:
        result = await db.execute(stmt.where(Workspace.slug == slug_or_id))
    return result.scalar_one_or_none()


async def ensure_uc_catalog(uc: UCClient, slug: str, *, default_schema: str = "main") -> None:
    """Lazily create the workspace's UC catalog and `main` schema.

    Idempotent: any UCConflictError from create_catalog/create_schema is
    treated as success. Used both by the eager `POST /workspaces` path
    (where the catalog won't exist yet) and as a self-heal for any
    workspace rows that pre-date M3.
    """
    if not await uc.catalog_exists(slug):
        try:
            await uc.create_catalog(slug)
        except UCConflictError:
            pass
    try:
        await uc.create_schema(slug, default_schema)
    except UCConflictError:
        pass
