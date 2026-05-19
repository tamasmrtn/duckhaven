import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.workspace import Workspace, WorkspaceMember

ROLE_ORDER = {"reader": 0, "writer": 1, "owner": 2}


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
    try:
        ws_id = uuid.UUID(slug_or_id)
        result = await db.execute(select(Workspace).where(Workspace.id == ws_id))
    except ValueError:
        result = await db.execute(select(Workspace).where(Workspace.slug == slug_or_id))
    return result.scalar_one_or_none()
