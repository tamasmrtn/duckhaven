"""Cron schedules for unattended jobs.

A generic schedule resource: ``job_type`` discriminates the work (v1 implements
only ``"saved_query"``). Schedules are dispatched by the background scheduler loop
(``api.services.scheduler.scanner``); these routes are CRUD plus a per-schedule run
history, which reuses the ``queries`` audit rows tagged ``origin="scheduled"``.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Query as QueryParam
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.query import Query, Schedule
from api.models.user import User
from api.schemas.query import QueryOut, ScheduleCreate, ScheduleOut, ScheduleUpdate
from api.services.scheduler.cron import next_run, validate_cron
from api.services.workspace import assert_workspace_member, get_workspace

router = APIRouter()


async def _require_workspace(db: AsyncSession, ws: str, user: User, *, min_role: str | None = None):
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role=min_role)
    return workspace


def _validate_cron_or_422(expr: str) -> None:
    try:
        validate_cron(expr)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/workspaces/{ws}/schedules", response_model=list[ScheduleOut])
async def list_schedules(
    ws: str,
    saved_query_id: uuid.UUID | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Schedule]:
    workspace = await _require_workspace(db, ws, user)
    stmt = select(Schedule).where(Schedule.workspace_id == workspace.id)
    if saved_query_id is not None:
        stmt = stmt.where(Schedule.saved_query_id == saved_query_id)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/workspaces/{ws}/schedule-runs", response_model=list[QueryOut])
async def list_workspace_schedule_runs(
    ws: str,
    limit: int = QueryParam(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Query]:
    """Every scheduled run in the workspace, newest first — the global runs feed."""
    workspace = await _require_workspace(db, ws, user)
    return list(
        (
            await db.execute(
                select(Query)
                .where(Query.workspace_id == workspace.id, Query.schedule_id.isnot(None))
                .order_by(Query.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


@router.post("/workspaces/{ws}/schedules", status_code=201, response_model=ScheduleOut)
async def create_schedule(
    ws: str,
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Schedule:
    workspace = await _require_workspace(db, ws, user, min_role="writer")
    _validate_cron_or_422(body.cron)
    now = datetime.now(tz=UTC)
    schedule = Schedule(
        workspace_id=workspace.id,
        job_type=body.job_type,
        saved_query_id=body.saved_query_id,
        agent_id=body.agent_id,
        cron=body.cron,
        enabled=body.enabled,
        next_run_at=next_run(body.cron, now) if body.enabled else None,
        created_by=user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.patch("/workspaces/{ws}/schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    ws: str,
    schedule_id: uuid.UUID,
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Schedule:
    workspace = await _require_workspace(db, ws, user, min_role="writer")
    schedule = (
        await db.execute(
            select(Schedule).where(
                Schedule.id == schedule_id, Schedule.workspace_id == workspace.id
            )
        )
    ).scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    fields = body.model_dump(exclude_unset=True)
    if "cron" in fields:
        _validate_cron_or_422(fields["cron"])
    for key, value in fields.items():
        setattr(schedule, key, value)
    # Recompute the next run when the cadence changes or the schedule is (re)enabled.
    if schedule.enabled:
        if "cron" in fields or ("enabled" in fields and schedule.next_run_at is None):
            schedule.next_run_at = next_run(schedule.cron, datetime.now(tz=UTC))
    else:
        schedule.next_run_at = None
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.delete("/workspaces/{ws}/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    ws: str,
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    workspace = await _require_workspace(db, ws, user, min_role="writer")
    schedule = (
        await db.execute(
            select(Schedule).where(
                Schedule.id == schedule_id, Schedule.workspace_id == workspace.id
            )
        )
    ).scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    await db.delete(schedule)
    await db.commit()


@router.get("/workspaces/{ws}/schedules/{schedule_id}/runs", response_model=list[QueryOut])
async def list_schedule_runs(
    ws: str,
    schedule_id: uuid.UUID,
    limit: int = QueryParam(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Query]:
    workspace = await _require_workspace(db, ws, user)
    return list(
        (
            await db.execute(
                select(Query)
                .where(Query.schedule_id == schedule_id, Query.workspace_id == workspace.id)
                .order_by(Query.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
