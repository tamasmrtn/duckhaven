import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_admin_user, get_db
from api.models.query import Query as QueryModel
from api.models.user import User
from api.schemas.query import QueryOut

router = APIRouter()


@router.get("/audit", response_model=list[QueryOut])
async def list_audit(
    workspace_id: uuid.UUID | None = Query(default=None),
    agent_id: uuid.UUID | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> list[QueryModel]:
    stmt = select(QueryModel).order_by(QueryModel.started_at.desc()).limit(limit)
    if workspace_id:
        stmt = stmt.where(QueryModel.workspace_id == workspace_id)
    if agent_id:
        stmt = stmt.where(QueryModel.agent_id == agent_id)
    if since:
        stmt = stmt.where(QueryModel.started_at >= since)
    if until:
        stmt = stmt.where(QueryModel.started_at <= until)
    result = await db.execute(stmt)
    return list(result.scalars().all())
