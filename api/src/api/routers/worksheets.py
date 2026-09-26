"""A user's worksheets: private, autosaved SQL editor tabs.

Every route is scoped to the caller. Another member's worksheet answers 404,
exactly like one that does not exist, since a worksheet is a personal draft.
Any member may keep worksheets, readers included: they run queries too.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi import Query as QueryParam
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.query import Query, SavedQuery
from api.models.user import User
from api.models.worksheet import Worksheet
from api.models.workspace import Workspace
from api.schemas.page import Page
from api.schemas.worksheet import WorksheetCreate, WorksheetOut, WorksheetUpdate
from api.services.agent_access import assert_can_assign_agent
from api.services.paging import paginate
from api.services.workspace import assert_workspace_member, get_workspace

router = APIRouter()

#: Fields whose change is an edit to the worksheet's content. They are guarded by
#: `version`; everything else is last-write-wins metadata.
CONTENT_FIELDS = ("sql", "title")
#: Fields a client may clear with an explicit null.
NULLABLE_FIELDS = ("agent_id", "catalog", "timeout_s", "saved_query_id", "last_query_id")


async def _workspace_for_member(db: AsyncSession, ws: str, user: User) -> Workspace:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    return workspace


async def _own_worksheet(
    db: AsyncSession, workspace: Workspace, user: User, worksheet_id: uuid.UUID
) -> Worksheet:
    sheet = (
        await db.execute(
            select(Worksheet).where(
                Worksheet.id == worksheet_id,
                Worksheet.workspace_id == workspace.id,
                Worksheet.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if sheet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worksheet not found")
    return sheet


async def _next_position(db: AsyncSession, workspace: Workspace, user: User) -> int:
    """One past the rightmost open tab, so a new or reopened tab lands at the end."""
    current = await db.scalar(
        select(func.max(Worksheet.tab_position)).where(
            Worksheet.workspace_id == workspace.id,
            Worksheet.owner_id == user.id,
            Worksheet.is_open.is_(True),
        )
    )
    return 0 if current is None else current + 1


async def _assert_saved_query_in_workspace(
    db: AsyncSession, workspace: Workspace, saved_query_id: uuid.UUID | None
) -> None:
    if saved_query_id is None:
        return
    found = await db.scalar(
        select(SavedQuery.id).where(
            SavedQuery.id == saved_query_id, SavedQuery.workspace_id == workspace.id
        )
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "saved_query_not_found",
                "detail": "No saved query with that id in this workspace",
            },
        )


async def _assert_query_in_workspace(
    db: AsyncSession, workspace: Workspace, query_id: uuid.UUID | None
) -> None:
    if query_id is None:
        return
    found = await db.scalar(
        select(Query.id).where(Query.id == query_id, Query.workspace_id == workspace.id)
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "query_not_found",
                "detail": "No query with that id in this workspace",
            },
        )


@router.get("/workspaces/{workspace}/worksheets", response_model=Page[WorksheetOut])
async def list_worksheets(
    ws: Annotated[str, Path(alias="workspace")],
    status_: Annotated[
        list[Literal["open", "closed"]] | None,
        QueryParam(alias="status", description="Only open tabs, only closed ones, or both."),
    ] = None,
    saved_query_id: uuid.UUID | None = QueryParam(default=None),
    q: str | None = QueryParam(
        default=None, max_length=255, description="Case-insensitive title substring."
    ),
    sort: Literal["updated_at", "title", "position"] = QueryParam(default="updated_at"),
    dir_: Literal["asc", "desc"] | None = QueryParam(
        default=None,
        alias="dir",
        description="Defaults to newest first for `updated_at`, ascending otherwise.",
    ),
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[WorksheetOut]:
    """The caller's worksheets in this workspace, open tabs and closed ones."""
    workspace = await _workspace_for_member(db, ws, user)
    stmt = select(Worksheet).where(
        Worksheet.workspace_id == workspace.id, Worksheet.owner_id == user.id
    )
    wanted = set(status_ or [])
    if wanted == {"open"}:
        stmt = stmt.where(Worksheet.is_open.is_(True))
    elif wanted == {"closed"}:
        stmt = stmt.where(Worksheet.is_open.is_(False))
    if saved_query_id is not None:
        stmt = stmt.where(Worksheet.saved_query_id == saved_query_id)
    if q:
        stmt = stmt.where(Worksheet.title.ilike(f"%{q}%"))

    column = {
        "updated_at": Worksheet.updated_at,
        "title": Worksheet.title,
        "position": Worksheet.tab_position,
    }[sort]
    descending = dir_ == "desc" if dir_ is not None else sort == "updated_at"
    order = (
        [column.desc(), Worksheet.id.desc()] if descending else [column.asc(), Worksheet.id.asc()]
    )
    rows, next_cursor, has_more = await paginate(db, stmt, sort=order, limit=limit, cursor=cursor)
    return Page[WorksheetOut](
        items=[WorksheetOut.model_validate(row[0]) for row in rows],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/workspaces/{workspace}/worksheets", status_code=201, response_model=WorksheetOut)
async def create_worksheet(
    ws: Annotated[str, Path(alias="workspace")],
    body: WorksheetCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Worksheet:
    """Create a worksheet owned by the caller. An open one is appended as the last tab."""
    workspace = await _workspace_for_member(db, ws, user)
    await _assert_saved_query_in_workspace(db, workspace, body.saved_query_id)
    await assert_can_assign_agent(db, user, body.agent_id)
    sheet = Worksheet(
        workspace_id=workspace.id,
        owner_id=user.id,
        title=body.title,
        sql=body.sql,
        agent_id=body.agent_id,
        catalog=body.catalog,
        timeout_s=body.timeout_s,
        saved_query_id=body.saved_query_id,
        is_open=body.is_open,
        tab_position=await _next_position(db, workspace, user) if body.is_open else 0,
        version=1,
    )
    db.add(sheet)
    await db.commit()
    await db.refresh(sheet)
    return sheet


@router.get("/workspaces/{workspace}/worksheets/{worksheet_id}", response_model=WorksheetOut)
async def get_worksheet(
    ws: Annotated[str, Path(alias="workspace")],
    worksheet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Worksheet:
    """One of the caller's worksheets, open or closed."""
    workspace = await _workspace_for_member(db, ws, user)
    return await _own_worksheet(db, workspace, user, worksheet_id)


@router.patch("/workspaces/{workspace}/worksheets/{worksheet_id}", response_model=WorksheetOut)
async def update_worksheet(
    ws: Annotated[str, Path(alias="workspace")],
    worksheet_id: uuid.UUID,
    body: WorksheetUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Worksheet:
    """Autosave a worksheet's content, or change its metadata.

    Changing `sql` or `title` requires `base_version`. If the worksheet moved on
    since that version -- another window saved it -- the answer is 409
    `worksheet_conflict`, with the current worksheet under `details.current` so the
    client can offer to load it or keep its own edit. A content change bumps
    `version` and `updated_at`; metadata changes bump neither."""
    workspace = await _workspace_for_member(db, ws, user)
    sheet = await _own_worksheet(db, workspace, user, worksheet_id)
    fields = {
        key: getattr(body, key)
        for key in body.model_fields_set
        if key != "base_version" and (getattr(body, key) is not None or key in NULLABLE_FIELDS)
    }

    content = {key: fields[key] for key in CONTENT_FIELDS if key in fields}
    if content:
        if body.base_version is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "base_version_required",
                    "detail": "Editing sql or title needs the base_version it was made against",
                },
            )
        if body.base_version != sheet.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "worksheet_conflict",
                    "detail": "The worksheet changed elsewhere since that version",
                    "current": WorksheetOut.model_validate(sheet).model_dump(mode="json"),
                },
            )

    if "saved_query_id" in fields:
        await _assert_saved_query_in_workspace(db, workspace, fields["saved_query_id"])
    if "last_query_id" in fields:
        await _assert_query_in_workspace(db, workspace, fields["last_query_id"])
    if "agent_id" in fields:
        await assert_can_assign_agent(db, user, fields["agent_id"])
    if fields.get("is_open") is True and not sheet.is_open and "tab_position" not in fields:
        fields["tab_position"] = await _next_position(db, workspace, user)

    for key, value in fields.items():
        setattr(sheet, key, value)
    if content:
        sheet.version += 1
        sheet.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(sheet)
    return sheet


@router.delete("/workspaces/{workspace}/worksheets/{worksheet_id}", status_code=204)
async def delete_worksheet(
    ws: Annotated[str, Path(alias="workspace")],
    worksheet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete one of the caller's worksheets. A linked saved query is unaffected."""
    workspace = await _workspace_for_member(db, ws, user)
    sheet = await _own_worksheet(db, workspace, user, worksheet_id)
    await db.delete(sheet)
    await db.commit()
