"""Coarse write-capability signal for the assistant (UX only).

Whether the assistant *offers* writes is a hint derived from the service account's
membership role and grants. It is not an authorization decision — the real gate is
server-side (``sql_guard`` + ``assert_query_access``). Being permissive here only
means the model may attempt a write and receive a governed 403; being conservative
means it won't try. Either way access cannot exceed the grants.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.workspace import WorkspaceMember

_WRITER_ROLES = {"writer", "owner"}


async def service_account_can_write(
    db: AsyncSession, workspace_id: uuid.UUID, service_account_id: uuid.UUID
) -> bool:
    """True if the service account could plausibly run a write in this workspace."""
    member = (
        await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == service_account_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        return False

    # Catalogs attached to this workspace, and whether any is "open" (role governs).
    attachments = (
        await db.execute(
            select(WorkspaceCatalog.catalog_id, WorkspaceCatalog.access_mode).where(
                WorkspaceCatalog.workspace_id == workspace_id
            )
        )
    ).all()
    catalog_ids = [row.catalog_id for row in attachments]
    has_open = any(row.access_mode == "open" for row in attachments)

    if member.role in _WRITER_ROLES and has_open:
        return True

    if not catalog_ids:
        return False
    writer_grant = (
        await db.execute(
            select(CatalogGrant.id).where(
                CatalogGrant.user_id == service_account_id,
                CatalogGrant.catalog_id.in_(catalog_ids),
                CatalogGrant.tier == "writer",
            )
        )
    ).first()
    return writer_grant is not None
