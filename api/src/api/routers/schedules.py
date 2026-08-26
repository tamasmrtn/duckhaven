"""Cron schedules for unattended jobs.

A generic schedule resource: ``job_type`` discriminates the work (v1 implements
only ``"saved_query"``). Schedules are dispatched by the background scheduler loop
(``api.services.scheduler.scanner``); these routes are CRUD plus a per-schedule run
history, which reuses the ``queries`` audit rows tagged ``origin="scheduled"``.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi import Query as QueryParam
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.query import Query, Schedule
from api.models.user import User
from api.schemas.page import Page
from api.schemas.query import QueryOut, ScheduleCreate, ScheduleOut, ScheduleUpdate
from api.services.agent_access import assert_can_assign_agent
from api.services.paging import paginate
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


@router.get("/workspaces/{workspace}/schedules", response_model=list[ScheduleOut])
async def list_schedules(
    ws: Annotated[str, Path(alias="workspace")],
    saved_query_id: uuid.UUID | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Schedule]:
    """The workspace's schedules. Filter by `saved_query_id` for one query's.

    A schedule is a generic job with a `job_type`; running a saved query is the
    only type today."""
    workspace = await _require_workspace(db, ws, user)
    stmt = select(Schedule).where(Schedule.workspace_id == workspace.id)
    if saved_query_id is not None:
        stmt = stmt.where(Schedule.saved_query_id == saved_query_id)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/workspaces/{workspace}/schedule-runs", response_model=Page[QueryOut])
async def list_workspace_schedule_runs(
    ws: Annotated[str, Path(alias="workspace")],
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[QueryOut]:
    """Every scheduled run in the workspace, newest first — the global runs feed."""
    workspace = await _require_workspace(db, ws, user)
    rows, next_cursor, has_more = await paginate(
        db,
        select(Query).where(Query.workspace_id == workspace.id, Query.schedule_id.isnot(None)),
        sort=[Query.started_at.desc(), Query.id.desc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[QueryOut](
        items=[QueryOut.model_validate(r[0], from_attributes=True) for r in rows],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/workspaces/{workspace}/schedules", status_code=201, response_model=ScheduleOut)
async def create_schedule(
    ws: Annotated[str, Path(alias="workspace")],
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Schedule:
    """Schedule a job on a cron expression. Requires `writer`.

    422 for an unparseable cron. You may only point a schedule at an agent you
    could run on yourself, and that check runs again at dispatch against the
    creator -- so revoking their access stops the runs too."""
    workspace = await _require_workspace(db, ws, user, min_role="writer")
    _validate_cron_or_422(body.cron)
    # You may only point a schedule at an agent you could run on yourself. This is
    # the fast feedback path; the same check runs again at dispatch against the
    # schedule's creator, so revoking access later stops the runs too.
    await assert_can_assign_agent(db, user, body.agent_id)
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


@router.patch("/workspaces/{workspace}/schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    ws: Annotated[str, Path(alias="workspace")],
    schedule_id: uuid.UUID,
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Schedule:
    """Change a schedule's cron, agent, or enabled state. Requires `writer`.

    A partial update: omitted fields are left alone. Disabling with
    `enabled=false` stops future runs without discarding the run history."""
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
    if "agent_id" in fields:
        await assert_can_assign_agent(db, user, fields["agent_id"])
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


@router.delete("/workspaces/{workspace}/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    ws: Annotated[str, Path(alias="workspace")],
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a schedule. Requires `writer`.

    Runs it already produced stay in the query history."""
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


@router.get("/workspaces/{workspace}/schedules/{schedule_id}/runs", response_model=Page[QueryOut])
async def list_schedule_runs(
    ws: Annotated[str, Path(alias="workspace")],
    schedule_id: uuid.UUID,
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[QueryOut]:
    """One schedule's runs, newest first -- did last night's job succeed?

    Each run is an ordinary query, so its rows, profile and error are readable
    through the `/queries/{query_id}` routes."""
    workspace = await _require_workspace(db, ws, user)
    rows, next_cursor, has_more = await paginate(
        db,
        select(Query).where(Query.schedule_id == schedule_id, Query.workspace_id == workspace.id),
        sort=[Query.started_at.desc(), Query.id.desc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[QueryOut](
        items=[QueryOut.model_validate(r[0], from_attributes=True) for r in rows],
        cursor=next_cursor,
        has_more=has_more,
    )
