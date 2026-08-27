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
from api.services.assistant.identity import AssistantIdentityError, resolve_service_account

_WRITER_ROLES = {"writer", "owner"}


async def _workspace_member(
    db: AsyncSession, workspace_id: uuid.UUID, service_account_id: uuid.UUID
) -> WorkspaceMember | None:
    return (
        await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == service_account_id,
            )
        )
    ).scalar_one_or_none()


async def assistant_has_workspace_access(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Whether the assistant's account can do anything at all in this workspace.

    Membership is the coarse gate every loopback call passes through, so without
    it every tool call comes back denied. Worth answering before a turn starts —
    the alternative is spending a whole model run to discover it. A missing or
    disabled account reads as no access for the same reason.
    """
    try:
        account = await resolve_service_account(db)
    except AssistantIdentityError:
        return False
    return await _workspace_member(db, workspace_id, account.id) is not None


async def service_account_can_write(
    db: AsyncSession, workspace_id: uuid.UUID, service_account_id: uuid.UUID
) -> bool:
    """True if the service account could plausibly run a write in this workspace."""
    member = await _workspace_member(db, workspace_id, service_account_id)
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
