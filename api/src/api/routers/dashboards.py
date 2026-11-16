"""Dashboards: the file format's schema, and each workspace's stored dashboards.

A dashboard has a published file, which readers see, and a draft, which writers
edit. Saving replaces the draft; publishing moves it into the published file and
records a revision. Publishing and deleting are ``owner``, as in the semantic
layer, because they change what every reader of the workspace sees.

A dashboard nobody has published is invisible to readers: they get the same 404
as for one that does not exist. A dashboard is addressed by its name, and an old
name keeps working after a rename.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.deps import _bearer, get_current_user, get_db, get_polaris_client, get_session_factory
from api.models.catalog import Catalog
from api.models.dashboard import (
    Dashboard,
    DashboardDraftSave,
    DashboardPreview,
    DashboardProposal,
    DashboardRef,
    DashboardRevision,
)
from api.models.user import User
from api.schemas.dashboard import (
    DashboardApplyOut,
    DashboardChangeOut,
    DashboardCreate,
    DashboardIn,
    DashboardOut,
    DashboardPreviewOut,
    DashboardPreviewRunIn,
    DashboardProposalIn,
    DashboardProposalOut,
    DashboardPublish,
    DashboardRevisionOut,
    DashboardRevisionSummary,
    DashboardRunIn,
    DashboardSummary,
    DashboardUpdate,
    DashboardValidationOut,
    DiagnosticOut,
    RefOut,
)
from api.schemas.page import Page
from api.services import delegation
from api.services.dashboards import loader, proposals, store, validate
from api.services.dashboards import run as run_service
from api.services.dashboards.spec import (
    SPEC_VERSION,
    SUPPORTED_SPEC_VERSIONS,
    DashboardSpec,
    json_schema,
)
from api.services.paging import paginate
from api.services.polaris import PolarisClient
from api.services.semantic.timespec import today_in
from api.services.sql_sessions.client_info import parse_user_agent
from api.services.workspace import (
    ROLE_ORDER,
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)

router = APIRouter()


@router.get("/dashboard-schema", response_class=JSONResponse)
async def get_dashboard_schema(
    version: int = Query(default=SPEC_VERSION, description="The format version to describe."),
) -> JSONResponse:
    """The JSON Schema of a dashboard file. Unauthenticated.

    Point an editor's YAML language server at it for completion and inline
    errors. It covers each file's shape; references between parts of the file,
    such as a `$name` naming a declared parameter, are checked when the
    dashboard is validated. A version this server does not read is a 422
    `unsupported_spec_version`."""
    if version not in SUPPORTED_SPEC_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "unsupported_spec_version",
                "detail": (
                    f"This server reads dashboard format version "
                    f"{', '.join(map(str, SUPPORTED_SPEC_VERSIONS))}, not {version}."
                ),
            },
        )
    return JSONResponse(json_schema(), headers={"Cache-Control": "public, max-age=3600"})


# ── Request context ───────────────────────────────────────────────────────────


class _Context:
    def __init__(self, workspace, user: User, catalogs: list[Catalog], role: str) -> None:
        self.workspace = workspace
        self.user = user
        self.catalogs = catalogs
        self.role = role

    def at_least(self, role: str) -> bool:
        return ROLE_ORDER.get(self.role, -1) >= ROLE_ORDER[role]


def _context(min_role: str):
    async def _dep(
        ws: Annotated[str, Path(alias="workspace")],
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> _Context:
        workspace = await get_workspace(db, ws)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        member = await assert_workspace_member(db, workspace.id, user.id, min_role=min_role)
        catalogs = await resolve_workspace_catalogs(db, workspace.id)
        return _Context(workspace, user, catalogs, member.role)

    return _dep


_DRAFT = Query(
    default=False,
    description="Show the draft, when there is one. Needs the writer role.",
)


def _assert_may_see_drafts(ctx: _Context, draft: bool) -> None:
    if draft and not ctx.at_least("writer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Reading a draft needs the writer role."
        )


async def _dashboard(db: AsyncSession, ctx: _Context, name: str) -> Dashboard:
    row = await store.find(db, ctx.workspace.id, name)
    if row is None or (row.status == "draft" and not ctx.at_least("writer")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No dashboard {name!r}.")
    return row


# ── Reading and writing files ─────────────────────────────────────────────────


def _parse(body: DashboardIn) -> loader.Loaded:
    if body.source_text is not None:
        return loader.load(body.source_text)
    if len(json.dumps(body.spec, ensure_ascii=False).encode()) > loader.MAX_BYTES:
        raise loader.DashboardInvalid(
            [
                loader.Diagnostic(
                    "", None, None, f"A dashboard is at most {loader.MAX_BYTES // 1024} KB."
                )
            ]
        )
    return loader.parse(body.spec)


def _invalid(code: str, diagnostics: list[loader.Diagnostic]) -> HTTPException:
    first = diagnostics[0]
    where = f" at {first.path}" if first.path else ""
    message = (
        first.message
        if len(diagnostics) == 1
        else f"{len(diagnostics)} problems; the first{where}: {first.message}"
    )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error": code,
            "detail": message,
            "diagnostics": [d.as_dict() for d in diagnostics],
        },
    )


def _read(body: DashboardIn) -> loader.Loaded:
    """The file in ``body``, or a 422 listing every problem in it."""
    try:
        return _parse(body)
    except loader.DashboardInvalid as exc:
        raise _invalid(exc.code, exc.diagnostics) from exc


def _shown(row: Dashboard, *, draft: bool) -> tuple[dict, str | None, bool]:
    """The version to show: the draft when asked for, or when nothing is published."""
    if row.draft_spec is not None and (draft or row.spec is None):
        return row.draft_spec, row.draft_source_text, True
    return row.spec, row.source_text, False


def _loaded(data: dict, text: str | None) -> loader.Loaded:
    return loader.load(text) if text is not None else loader.parse(data)


def _diagnostics(items: list[loader.Diagnostic]) -> list[DiagnosticOut]:
    return [DiagnosticOut(**d.as_dict()) for d in items]


def _refs(spec: DashboardSpec) -> list[RefOut]:
    return [RefOut(kind=kind, model=model, name=name) for kind, model, name in store.refs(spec)]


def _out(row: Dashboard, *, draft: bool = False) -> DashboardOut:
    data, text, is_draft = _shown(row, draft=draft)
    loaded = _loaded(data, text)
    return DashboardOut(
        **DashboardSummary.model_validate(row).model_dump(),
        draft=is_draft,
        spec=store.stored(loaded.spec),
        source_text=text,
        spec_hash=store.spec_hash(loaded.spec),
        spec_version=loaded.spec.version,
        source_ref=row.source_ref,
        source_hash=row.source_hash,
        client=row.client,
        created_by=row.created_by,
        published_by=row.published_by,
        refs=_refs(loaded.spec),
        warnings=_diagnostics(loaded.warnings),
    )


async def _named(db: AsyncSession, out: DashboardOut) -> DashboardOut:
    """``out`` with the name of the person who made the last change, as saved queries have."""
    if out.updated_by is not None:
        out.updated_by_name = await db.scalar(select(User.name).where(User.id == out.updated_by))
    return out


#: The User-Agent name of DuckHaven's own MCP server, which calls this API for an agent.
MCP_SERVER = "duckhaven-mcp"
#: The header in which the MCP server names the agent's client, such as
#: ``claude-code/2.1.0``. Its own User-Agent names the MCP server.
MCP_CLIENT_HEADER = "x-duckhaven-mcp-client"


def _provenance(request: Request) -> tuple[str, str | None]:
    """How a change arrived, and the client that sent it as ``name/version``."""
    name, version = parse_user_agent(request.headers.get("user-agent"))
    client = f"{name}/{version}" if name and version else name
    if _bearer(request.headers.get("authorization")) is None:
        return "ui", client  # a session cookie: the web app
    if name == "duckhaven-cli":
        return "cli", client
    if name == MCP_SERVER:
        return "mcp", request.headers.get(MCP_CLIENT_HEADER) or client
    return "rest", client


def _stamp(row: Dashboard, user: User, request: Request) -> None:
    """Record who made a change, and how it arrived: the web app, the CLI, MCP or the API."""
    via, client = _provenance(request)
    row.updated_by, row.updated_via, row.client = user.id, via, client[:255] if client else None
    row.updated_at = datetime.now(UTC)


#: The window the hourly limit on drafts saved over MCP counts saves in.
DRAFT_SAVES_WINDOW = timedelta(hours=1)


def _quota_exceeded(quota: str, limit: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"error": "draft_quota_exceeded", "detail": message, "quota": quota, "limit": limit},
    )


async def _assert_draft_quota(db: AsyncSession, ctx: _Context, row: Dashboard | None) -> None:
    """Refuse a draft saved over MCP past the person's limits in the workspace.

    ``row`` is the dashboard being saved, or None for a new one. A dashboard
    whose draft this person last saved over MCP is counted already, so saving
    it again takes only an hourly save."""
    now = datetime.now(UTC)
    mine = (
        DashboardDraftSave.workspace_id == ctx.workspace.id,
        DashboardDraftSave.user_id == ctx.user.id,
        DashboardDraftSave.saved_at > now - DRAFT_SAVES_WINDOW,
    )
    saves, oldest = (
        await db.execute(select(func.count(), func.min(DashboardDraftSave.saved_at)).where(*mine))
    ).one()
    per_hour = settings.mcp_dashboard_draft_saves_per_hour
    if saves >= per_hour:
        if oldest.tzinfo is None:  # SQLite hands back naive UTC
            oldest = oldest.replace(tzinfo=UTC)
        wait = (oldest + DRAFT_SAVES_WINDOW - now).total_seconds()
        minutes = max(1, math.ceil(wait / 60))
        raise _quota_exceeded(
            "saves_per_hour",
            per_hour,
            f"You saved {saves} dashboard drafts over MCP in the last hour, the most allowed. "
            f"Try again in {minutes} minutes, or save it in DuckHaven.",
        )
    counted = (
        row is not None
        and row.has_draft
        and row.updated_via == "mcp"
        and row.updated_by == ctx.user.id
    )
    if counted:
        return
    drafts = await db.scalar(
        select(func.count())
        .select_from(Dashboard)
        .where(
            Dashboard.workspace_id == ctx.workspace.id,
            Dashboard.updated_by == ctx.user.id,
            Dashboard.updated_via == "mcp",
            Dashboard.draft_spec.is_not(None),
        )
    )
    limit = settings.mcp_dashboard_drafts_per_user
    if drafts >= limit:
        raise _quota_exceeded(
            "drafts",
            limit,
            f"You have drafts of {drafts} dashboards saved over MCP in this workspace, the "
            "most allowed. Publish or discard some of them first.",
        )


async def _record_draft_save(db: AsyncSession, ctx: _Context, row: Dashboard) -> None:
    """Count a draft saved over MCP, and forget this person's saves older than the window."""
    now = datetime.now(UTC)
    await db.execute(
        delete(DashboardDraftSave).where(
            DashboardDraftSave.user_id == ctx.user.id,
            DashboardDraftSave.saved_at <= now - DRAFT_SAVES_WINDOW,
        )
    )
    db.add(
        DashboardDraftSave(
            workspace_id=ctx.workspace.id, user_id=ctx.user.id, dashboard_id=row.id, saved_at=now
        )
    )


async def _follow(db: AsyncSession, row: Dashboard, spec: DashboardSpec) -> None:
    """Copy name, title, description and labels from the file the listing shows."""
    await store.claim_name(db, row, spec.name)
    row.title, row.description, row.labels = spec.title, spec.description, list(spec.labels)
    row.spec_version = spec.version


async def _save_draft(
    db: AsyncSession,
    row: Dashboard,
    spec: DashboardSpec,
    source_text: str | None,
    user: User,
    request: Request,
) -> None:
    """Make ``spec`` the dashboard's draft, as an edit or an accepted proposal does.

    A new ``name`` renames a dashboard never published at once; a published one
    is renamed when the draft is published."""
    store.assert_writable(row)
    if row.spec is None:
        await _follow(db, row, spec)
    elif spec.name != row.name:
        await store.assert_name_free(db, row.workspace_id, spec.name, mine=row.id)
    row.draft_spec, row.draft_source_text = store.stored(spec), source_text
    row.version += 1
    _stamp(row, user, request)
    await store.set_refs(db, row, "draft", spec)


async def _assert_base(db: AsyncSession, row: Dashboard, base_version: int) -> None:
    if base_version != row.version:
        current = await _named(db, _out(row, draft=True))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dashboard_conflict",
                "detail": (
                    f"{row.name!r} changed since version {base_version}; "
                    f"it is at version {row.version}."
                ),
                "current": current.model_dump(mode="json"),
            },
        )


# ── Dashboards ────────────────────────────────────────────────────────────────


@router.get("/workspaces/{workspace}/dashboards", response_model=Page[DashboardSummary])
async def list_dashboards(
    status_: Annotated[
        Literal["draft", "published"] | None,
        Query(alias="status", description="`draft` lists those never published."),
    ] = None,
    source: Literal["ui", "git"] | None = None,
    source_id: str | None = Query(default=None, max_length=255),
    q: str | None = Query(default=None, max_length=200, description="Part of a name or title."),
    uses: str | None = Query(
        default=None,
        max_length=511,
        description="Dashboards that read a model (`orders`) or one of its metrics or "
        "dimensions (`orders.revenue`).",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = None,
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> Page[DashboardSummary]:
    """The workspace's dashboards, by name.

    Readers see published dashboards; writers also see those never published."""
    stmt = select(Dashboard).where(Dashboard.workspace_id == ctx.workspace.id)
    writer = ctx.at_least("writer")
    if not writer:
        stmt = stmt.where(Dashboard.status == "published")
    if status_ is not None:
        stmt = stmt.where(Dashboard.status == status_)
    if source is not None:
        stmt = stmt.where(Dashboard.source == source)
    if source_id is not None:
        stmt = stmt.where(Dashboard.source_id == source_id)
    if q:
        stmt = stmt.where(or_(Dashboard.name.ilike(f"%{q}%"), Dashboard.title.ilike(f"%{q}%")))
    if uses:
        model, _, member = uses.partition(".")
        refs = select(DashboardRef.dashboard_id).where(
            DashboardRef.workspace_id == ctx.workspace.id,
            DashboardRef.state.in_(("draft", "published") if writer else ("published",)),
        )
        if member:
            refs = refs.where(
                DashboardRef.kind.in_(("metric", "dimension")),
                DashboardRef.model == model,
                DashboardRef.name == member,
            )
        else:
            refs = refs.where(DashboardRef.kind == "model", DashboardRef.name == model)
        stmt = stmt.where(Dashboard.id.in_(refs))
    rows, next_cursor, has_more = await paginate(
        db, stmt, sort=[Dashboard.name.asc(), Dashboard.id.asc()], limit=limit, cursor=cursor
    )
    return Page[DashboardSummary](
        items=[DashboardSummary.model_validate(row[0]) for row in rows],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/workspaces/{workspace}/dashboards", status_code=201, response_model=DashboardOut)
async def create_dashboard(
    body: DashboardCreate,
    request: Request,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardOut:
    """Create a dashboard as a draft, from its file.

    An invalid file is a 422 `invalid_dashboard` listing every problem under
    `details.diagnostics`. Problems only the workspace can find, such as a
    metric that does not exist, do not stop a draft; publishing checks them.
    Over MCP, a 429 `draft_quota_exceeded` past the limits on drafts."""
    spec = _read(body).spec
    over_mcp = _provenance(request)[0] == "mcp"
    if over_mcp:
        await _assert_draft_quota(db, ctx, None)
    row = Dashboard(
        workspace_id=ctx.workspace.id,
        status="draft",
        source="ui",
        version=1,
        created_by=ctx.user.id,
        draft_spec=store.stored(spec),
        draft_source_text=body.source_text,
    )
    await _follow(db, row, spec)
    _stamp(row, ctx.user, request)
    db.add(row)
    await db.flush()
    await store.set_refs(db, row, "draft", spec)
    if over_mcp:
        await _record_draft_save(db, ctx, row)
    await db.commit()
    await db.refresh(row)
    return await _named(db, _out(row, draft=True))


@router.get("/workspaces/{workspace}/dashboards/{dashboard}", response_model=DashboardOut)
async def get_dashboard(
    dashboard: str,
    draft: bool = _DRAFT,
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> DashboardOut:
    """One dashboard and its file: the published one, or with `draft=true` the draft.

    A dashboard never published shows its draft. `refs` lists what the file
    reads, and `managed` says whether it is managed in git."""
    _assert_may_see_drafts(ctx, draft)
    return await _named(db, _out(await _dashboard(db, ctx, dashboard), draft=draft))


@router.patch("/workspaces/{workspace}/dashboards/{dashboard}", response_model=DashboardOut)
async def update_dashboard(
    dashboard: str,
    body: DashboardUpdate,
    request: Request,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardOut:
    """Save a new draft. Readers keep seeing the published file until it is published.

    `base_version` is the `version` the edit was made against. If the dashboard
    changed since, the answer is 409 `dashboard_conflict` with the current draft
    under `details.current`. A new `name` in the file renames the dashboard when
    it is published, or at once if it never was, and the old name keeps
    working. Over MCP, a 429 `draft_quota_exceeded` past the limits on drafts."""
    row = await _dashboard(db, ctx, dashboard)
    store.assert_writable(row)
    await _assert_base(db, row, body.base_version)
    spec = _read(body).spec
    over_mcp = _provenance(request)[0] == "mcp"
    if over_mcp:
        await _assert_draft_quota(db, ctx, row)
    await _save_draft(db, row, spec, body.source_text, ctx.user, request)
    if over_mcp:
        await _record_draft_save(db, ctx, row)
    await db.commit()
    await db.refresh(row)
    return await _named(db, _out(row, draft=True))


@router.delete("/workspaces/{workspace}/dashboards/{dashboard}", status_code=204)
async def delete_dashboard(
    dashboard: str,
    ctx: _Context = Depends(_context("owner")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a dashboard, its draft and its revisions."""
    row = await _dashboard(db, ctx, dashboard)
    store.assert_writable(row)
    await db.delete(row)
    await db.commit()


@router.delete("/workspaces/{workspace}/dashboards/{dashboard}/draft", status_code=204)
async def discard_dashboard_draft(
    dashboard: str,
    request: Request,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Throw away the draft, going back to the published file.

    A dashboard never published is all draft, so this is a 409
    `dashboard_unpublished`: delete the dashboard instead."""
    row = await _dashboard(db, ctx, dashboard)
    store.assert_writable(row)
    if row.spec is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dashboard_unpublished",
                "detail": (
                    f"{row.name!r} has never been published, so its draft is all there is. "
                    "Delete the dashboard instead."
                ),
            },
        )
    if row.draft_spec is None:
        return
    row.draft_spec = row.draft_source_text = None
    row.version += 1
    _stamp(row, ctx.user, request)
    await store.set_refs(db, row, "draft", None)
    await db.commit()


@router.post("/workspaces/{workspace}/dashboards/{dashboard}/publish", response_model=DashboardOut)
async def publish_dashboard(
    dashboard: str,
    body: DashboardPublish,
    request: Request,
    ctx: _Context = Depends(_context("owner")),
    db: AsyncSession = Depends(get_db),
) -> DashboardOut:
    """Publish the draft, so readers see it, and record it as a revision.

    The draft is checked against the workspace as the publisher: every model
    is published and visible, every query compiles and every SQL widget is one
    `SELECT`. Any problem is a 422 `invalid_dashboard`. With no draft, nothing
    changes."""
    row = await _dashboard(db, ctx, dashboard)
    store.assert_writable(row)
    await _assert_base(db, row, body.base_version)
    if row.draft_spec is None:
        return await _named(db, _out(row))
    loaded = _loaded(row.draft_spec, row.draft_source_text)
    await _assert_publishable(db, ctx, loaded)
    row.version += 1
    _stamp(row, ctx.user, request)
    await _publish(db, ctx, row, loaded.spec, row.draft_source_text, body.message)
    await db.commit()
    await db.refresh(row)
    return await _named(db, _out(row))


async def _assert_publishable(db: AsyncSession, ctx: _Context, loaded: loader.Loaded) -> None:
    """Refuse a file the workspace finds faults in, as the caller, with a 422 listing them."""
    problems = await validate.problems(
        db,
        loaded,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        catalogs=ctx.catalogs,
        today=today_in(ctx.workspace.timezone),
    )
    if problems:
        raise _invalid("invalid_dashboard", problems)


async def _publish(
    db: AsyncSession,
    ctx: _Context,
    row: Dashboard,
    spec: DashboardSpec,
    text: str | None,
    message: str | None,
) -> None:
    """Make ``spec`` the published file, record it as a revision, and drop the draft."""
    await _follow(db, row, spec)
    last = (
        await db.execute(
            select(func.max(DashboardRevision.number)).where(
                DashboardRevision.dashboard_id == row.id
            )
        )
    ).scalar_one()
    now = datetime.now(UTC)
    row.spec, row.source_text = store.stored(spec), text
    row.spec_hash = store.spec_hash(spec)
    row.draft_spec = row.draft_source_text = None
    row.status, row.published_by, row.published_at = "published", ctx.user.id, now
    db.add(
        DashboardRevision(
            dashboard_id=row.id,
            number=(last or 0) + 1,
            spec=row.spec,
            source_text=row.source_text,
            spec_hash=row.spec_hash,
            message=message,
            published_by=ctx.user.id,
            published_at=now,
        )
    )
    await store.set_refs(db, row, "published", spec)
    await store.set_refs(db, row, "draft", None)


@router.get(
    "/workspaces/{workspace}/dashboards/{dashboard}/source",
    response_class=Response,
    responses={
        200: {
            "description": "The file as YAML.",
            "content": {"application/yaml": {"schema": {"type": "string"}}},
        }
    },
)
async def get_dashboard_source(
    dashboard: str,
    draft: bool = _DRAFT,
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The dashboard's file as YAML: as written, or as canonical YAML if it was sent as JSON.

    Loading the result as a file gives back the same dashboard."""
    _assert_may_see_drafts(ctx, draft)
    row = await _dashboard(db, ctx, dashboard)
    data, text, _ = _shown(row, draft=draft)
    return Response(
        text if text is not None else store.export(store.read(data)),
        media_type="application/yaml",
        headers={"Content-Disposition": f'inline; filename="{row.name}.dashboard.yml"'},
    )


# ── Applying from git ─────────────────────────────────────────────────────────

_FILE_BODY = {
    "requestBody": {
        "required": True,
        "description": "The dashboard file as YAML, byte for byte as it is in git.",
        "content": {"application/yaml": {"schema": {"type": "string"}}},
    }
}


def _source_changed(message: str, current_hash: str | None) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "source_changed", "detail": message, "current_hash": current_hash},
    )


@router.put(
    "/workspaces/{workspace}/dashboards/{dashboard}/source",
    response_model=DashboardApplyOut,
    responses={201: {"model": DashboardApplyOut, "description": "The dashboard was created."}},
    openapi_extra=_FILE_BODY,
)
async def apply_dashboard_source(
    dashboard: str,
    request: Request,
    response: Response,
    source_id: str | None = Query(
        default=None,
        max_length=255,
        description="Where the file lives, such as the repository. Kept when left out.",
    ),
    source_ref: str | None = Query(
        default=None, max_length=255, description="The commit the file comes from."
    ),
    expected_hash: str | None = Query(
        default=None,
        max_length=64,
        description=(
            "The `spec_hash` the dashboard's newest file must have, as a dry run's "
            "`current_hash` reports it. Otherwise 409 `source_changed`."
        ),
    ),
    publish: bool = Query(
        default=False, description="Publish the file. Needs the owner role, except on a dry run."
    ),
    adopt: bool = Query(
        default=False,
        description=(
            "Manage a dashboard made in DuckHaven from this file. Needs the owner role, "
            "except on a dry run."
        ),
    ),
    force: bool = Query(
        default=False,
        description="Apply even when `expected_hash` or `source_id` does not match.",
    ),
    dry_run: bool = Query(default=False, description="Make every check, and change nothing."),
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardApplyOut:
    """Apply a dashboard file from git: create the dashboard, or replace its file.

    The body is the file itself, kept byte for byte, comments included. The
    dashboard is then managed in git: every other write to it is a 409
    `managed_dashboard` until it is detached. Without `publish` the file becomes
    the draft. The answer says what changed, and a `dry_run` answers the same
    way, after the same checks, without changing anything.

    A dashboard not found is created under the file's `name`, which must match
    the path. One made in DuckHaven is a 409 `dashboard_name_taken` unless
    `adopt` is set. A file the same as the dashboard's changes nothing, and the
    answer's `action` is `unchanged`."""
    if (publish or adopt) and not dry_run and not ctx.at_least("owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Publishing or adopting a dashboard needs the owner role.",
        )
    raw = await request.body()
    try:
        loaded = loader.load(raw)
    except loader.DashboardInvalid as exc:
        raise _invalid(exc.code, exc.diagnostics) from exc
    spec, text = loaded.spec, raw.decode("utf-8")
    new_hash = store.spec_hash(spec)

    row = await store.find(db, ctx.workspace.id, dashboard)
    current_hash = None
    if row is None:
        if spec.name.lower() != dashboard.lower():
            message = f"The file is named {spec.name!r}, but was applied as {dashboard!r}."
            hint = f"Apply it as {spec.name!r}, or change its name."
            raise _invalid("invalid_dashboard", [loaded.at("name", message, hint)])
        if expected_hash is not None and not force:
            raise _source_changed(
                f"There is no dashboard {dashboard!r}, so its file cannot have that hash.", None
            )
    else:
        newest, newest_text, _ = _shown(row, draft=True)
        current_hash = store.spec_hash(store.read(newest))
        if row.source == "ui" and not adopt:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "dashboard_name_taken",
                    "detail": (
                        f"{row.name!r} is a dashboard made in DuckHaven. Apply with adopt=true "
                        "to manage it from this file."
                    ),
                },
            )
        moved = row.source == "git" and source_id and row.source_id not in (None, source_id)
        if moved and not force:
            raise _source_changed(
                f"{row.name!r} is applied from {row.source_id}, not {source_id}. Apply "
                "with force=true to manage it from here.",
                current_hash,
            )
        if expected_hash is not None and expected_hash != current_hash and not force:
            raise _source_changed(
                f"{row.name!r} changed: its file's hash is {current_hash}, not {expected_hash}. "
                "Read it again, or apply with force=true to replace it.",
                current_hash,
            )
        if spec.name != row.name:
            await store.assert_name_free(db, row.workspace_id, spec.name, mine=row.id)

    # Compared with what readers see when publishing, otherwise with the newest file.
    if row is None:
        old_data, old_text = None, None
    elif publish and row.spec is not None:
        old_data, old_text = row.spec, row.source_text
    else:
        old_data, old_text = newest, newest_text
    old = store.read(old_data) if old_data is not None else None
    found = store.changes(old, spec)
    if old is not None and not found and old_text != text:
        found = [("source_text", "changed")]
    unchanged = (
        row is not None
        and row.source == "git"
        and (source_id is None or source_id == row.source_id)
        and not found
        and (not publish or (row.draft_spec is None and row.spec is not None))
    )
    action = "create" if row is None else "unchanged" if unchanged else "update"
    renames = row is not None and spec.name != row.name and (publish or row.spec is None)
    plan = {
        "dry_run": dry_run,
        "action": action,
        "name": spec.name if row is None or renames else row.name,
        "renamed_from": row.name if renames else None,
        "adopted": row is not None and row.source == "ui",
        "published": publish and action != "unchanged",
        "current_hash": current_hash,
        "spec_hash": new_hash,
        "changes": [DashboardChangeOut(path=path, change=change) for path, change in found],
        "warnings": _diagnostics(loaded.warnings),
    }
    if publish and action != "unchanged":
        await _assert_publishable(db, ctx, loaded)
    if dry_run:
        return DashboardApplyOut(**plan, dashboard=None)
    if action == "unchanged":
        return DashboardApplyOut(**plan, dashboard=await _named(db, _out(row, draft=not publish)))

    if row is None:
        row = Dashboard(
            workspace_id=ctx.workspace.id, status="draft", version=1, created_by=ctx.user.id
        )
        await _follow(db, row, spec)
        db.add(row)
        response.status_code = status.HTTP_201_CREATED
    else:
        row.version += 1
    row.source, row.source_id = "git", source_id or row.source_id
    row.source_ref, row.source_hash = source_ref, hashlib.sha256(raw).hexdigest()
    _stamp(row, ctx.user, request)
    await db.flush()  # a new dashboard's id, for its refs and revision
    if publish:
        applied = " ".join(p for p in (row.source_id, source_ref and f"at {source_ref}") if p)
        message = f"Applied from {applied or 'git'}"[:500]
        await _publish(db, ctx, row, spec, text, message)
    else:
        if row.spec is None:
            await _follow(db, row, spec)
        row.draft_spec, row.draft_source_text = store.stored(spec), text
        await store.set_refs(db, row, "draft", spec)
    await db.commit()
    await db.refresh(row)
    return DashboardApplyOut(**plan, dashboard=await _named(db, _out(row, draft=not publish)))


@router.delete("/workspaces/{workspace}/dashboards/{dashboard}/source", status_code=204)
async def detach_dashboard_source(
    dashboard: str,
    request: Request,
    ctx: _Context = Depends(_context("owner")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Stop managing a dashboard in git, so it is edited in DuckHaven again.

    The dashboard keeps its files, draft and revisions; it forgets only where
    its file came from. A dashboard not managed in git is left as it is."""
    row = await _dashboard(db, ctx, dashboard)
    if row.source != "git":
        return
    row.source = "ui"
    row.source_id = row.source_ref = row.source_hash = None
    row.version += 1
    _stamp(row, ctx.user, request)
    await db.commit()


# ── Revisions ─────────────────────────────────────────────────────────────────


@router.get(
    "/workspaces/{workspace}/dashboards/{dashboard}/revisions",
    response_model=Page[DashboardRevisionSummary],
)
async def list_dashboard_revisions(
    dashboard: str,
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = None,
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> Page[DashboardRevisionSummary]:
    """Every publish of the dashboard, newest first."""
    row = await _dashboard(db, ctx, dashboard)
    stmt = select(DashboardRevision).where(DashboardRevision.dashboard_id == row.id)
    rows, next_cursor, has_more = await paginate(
        db,
        stmt,
        sort=[DashboardRevision.number.desc(), DashboardRevision.id.desc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[DashboardRevisionSummary](
        items=[DashboardRevisionSummary.model_validate(r[0]) for r in rows],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/workspaces/{workspace}/dashboards/{dashboard}/revisions/{revision}",
    response_model=DashboardRevisionOut,
)
async def get_dashboard_revision(
    dashboard: str,
    revision: Annotated[int, Path(ge=1, description="The revision number, from 1.")],
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> DashboardRevisionOut:
    """One published revision and its file, as it was published."""
    row = await _dashboard(db, ctx, dashboard)
    rev = (
        await db.execute(
            select(DashboardRevision).where(
                DashboardRevision.dashboard_id == row.id, DashboardRevision.number == revision
            )
        )
    ).scalar_one_or_none()
    if rev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{row.name!r} has no revision {revision}.",
        )
    return DashboardRevisionOut(
        **DashboardRevisionSummary.model_validate(rev).model_dump(),
        spec=store.stored(store.read(rev.spec)),
        source_text=rev.source_text,
    )


# ── Running ───────────────────────────────────────────────────────────────────

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post(
    "/workspaces/{workspace}/dashboards/{dashboard}/run",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "A server-sent event stream: `start`, then one `widget` event per data "
                "widget as it is answered, a `compute` event when compute has to start "
                "first, and `done`. An `error` event ends a run that could not go on."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
async def run_dashboard(
    dashboard: str,
    body: DashboardRunIn,
    request: Request,
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
    """Run a dashboard as you, and stream each widget's data as it arrives.

    Every widget's query runs with your grants, through the same checks and
    result cache as a worksheet's run, so two people can see different numbers
    on one dashboard. Widgets that ask the same question of one model share a
    query; a result the cache holds is sent at once, without starting compute.
    A parameter value that does not fit is a 422 `invalid_parameters`, and a
    widget id the dashboard lacks a 422 `unknown_widget`. Closing the stream
    cancels the queries still running."""
    _assert_may_see_drafts(ctx, body.draft)
    row = await _dashboard(db, ctx, dashboard)
    data, text, is_draft = _shown(row, draft=body.draft)
    spec = _loaded(data, text).spec
    about = {
        "name": row.name,
        "version": row.version,
        "spec_hash": store.spec_hash(spec),
        "draft": is_draft,
        "preview": None,
    }
    return await _run(db, ctx, request, polaris, session_factory, spec, about, body)


async def _run(
    db: AsyncSession,
    ctx: _Context,
    request: Request,
    polaris: PolarisClient,
    session_factory: async_sessionmaker[AsyncSession],
    spec: DashboardSpec,
    about: dict,
    body: DashboardPreviewRunIn,
) -> StreamingResponse:
    try:
        plan = await run_service.plan(
            db,
            workspace=ctx.workspace,
            catalogs=ctx.catalogs,
            user=ctx.user,
            spec=spec,
            dashboard=about,
            today=today_in(ctx.workspace.timezone),
            values=body.params,
            widget_ids=body.widgets,
            use_cache=body.use_cache,
            client=parse_user_agent(request.headers.get("user-agent")),
            compare=body.compare,
        )
    except run_service.ParameterError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_parameters", "detail": str(exc), "parameter": exc.name},
        ) from exc
    except run_service.UnknownWidget as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "unknown_widget", "detail": str(exc)},
        ) from exc
    # The plan holds only plain values: the stream opens sessions of its own.
    await db.commit()
    return StreamingResponse(
        run_service.stream(plan, session_factory, polaris),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ── Previews ──────────────────────────────────────────────────────────────────

#: How long a preview lasts after it was last changed or run.
PREVIEW_TTL = timedelta(hours=2)

_STREAM = {
    200: {
        "description": "The same server-sent event stream as running a dashboard.",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
}


def _preview_out(row: DashboardPreview) -> DashboardPreviewOut:
    loaded = _loaded(row.spec, row.source_text)
    return DashboardPreviewOut(
        id=row.id,
        spec=store.stored(loaded.spec),
        source_text=row.source_text,
        spec_hash=row.spec_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        warnings=_diagnostics(loaded.warnings),
    )


def _hold(row: DashboardPreview, body: DashboardIn) -> None:
    """Replace the preview's file with the one in ``body``, or refuse it with a 422."""
    spec = _read(body).spec
    now = datetime.now(UTC)
    row.spec, row.source_text = store.stored(spec), body.source_text
    row.spec_hash = store.spec_hash(spec)
    row.updated_at, row.expires_at = now, now + PREVIEW_TTL


async def _preview(db: AsyncSession, ctx: _Context, preview_id: uuid.UUID) -> DashboardPreview:
    """The caller's own live preview. Anyone else's is a 404, as is one that expired."""
    row = await db.scalar(
        select(DashboardPreview).where(
            DashboardPreview.id == preview_id,
            DashboardPreview.workspace_id == ctx.workspace.id,
            DashboardPreview.created_by == ctx.user.id,
            DashboardPreview.expires_at > datetime.now(UTC),
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No preview {preview_id}. A preview expires two hours after it was last used.",
        )
    return row


@router.post(
    "/workspaces/{workspace}/dashboard-previews",
    status_code=201,
    response_model=DashboardPreviewOut,
)
async def create_dashboard_preview(
    body: DashboardIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardPreviewOut:
    """Hold a dashboard file to try out, without saving it as a dashboard.

    Only you can read or run a preview, and it runs as you. Replace its file
    with `PUT` as you edit it. It expires two hours after it was last changed
    or run. An invalid file is a 422 `invalid_dashboard`, as when saving one."""
    row = DashboardPreview(workspace_id=ctx.workspace.id, created_by=ctx.user.id)
    _hold(row, body)
    row.created_at = row.updated_at
    await db.execute(delete(DashboardPreview).where(DashboardPreview.expires_at <= row.updated_at))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _preview_out(row)


@router.get(
    "/workspaces/{workspace}/dashboard-previews/{dashboard_preview_id}",
    response_model=DashboardPreviewOut,
)
async def get_dashboard_preview(
    dashboard_preview_id: uuid.UUID,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardPreviewOut:
    """Your preview and its file. `spec_hash` changes whenever the file does."""
    return _preview_out(await _preview(db, ctx, dashboard_preview_id))


@router.put(
    "/workspaces/{workspace}/dashboard-previews/{dashboard_preview_id}",
    response_model=DashboardPreviewOut,
)
async def replace_dashboard_preview(
    dashboard_preview_id: uuid.UUID,
    body: DashboardIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardPreviewOut:
    """Replace your preview's file. An invalid file is a 422 `invalid_dashboard`,
    and the preview keeps the file it had."""
    row = await _preview(db, ctx, dashboard_preview_id)
    _hold(row, body)
    await db.commit()
    await db.refresh(row)
    return _preview_out(row)


@router.post(
    "/workspaces/{workspace}/dashboard-previews/{dashboard_preview_id}/run",
    response_class=StreamingResponse,
    responses=_STREAM,
)
async def run_dashboard_preview(
    dashboard_preview_id: uuid.UUID,
    body: DashboardPreviewRunIn,
    request: Request,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
    """Run your preview as you, and stream each widget's data as it arrives.

    It runs as a dashboard does, with your grants, and its queries are recorded
    in History under the file's name with the tag `dashboard.preview`. The
    preview lasts another two hours."""
    row = await _preview(db, ctx, dashboard_preview_id)
    spec = _loaded(row.spec, row.source_text).spec
    row.expires_at = datetime.now(UTC) + PREVIEW_TTL
    about = {
        "name": spec.name,
        "version": None,
        "spec_hash": row.spec_hash,
        "draft": False,
        "preview": str(row.id),
    }
    return await _run(db, ctx, request, polaris, session_factory, spec, about, body)


# ── Proposals ─────────────────────────────────────────────────────────────────

#: How long a proposal waits for its person to decide.
PROPOSAL_TTL = timedelta(days=1)


def _proposal_status(proposal: DashboardProposal, row: Dashboard) -> str:
    if proposal.status != "pending":
        return proposal.status
    expires = proposal.expires_at
    if expires.tzinfo is None:  # SQLite hands back naive UTC
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        return "expired"
    return "stale" if row.version != proposal.base_version else "pending"


def _proposal_out(proposal: DashboardProposal, row: Dashboard) -> DashboardProposalOut:
    loaded = loader.parse(proposal.result_spec)
    return DashboardProposalOut(
        id=proposal.id,
        dashboard=row.name,
        title=loaded.spec.title,
        status=_proposal_status(proposal, row),
        summary=proposal.summary,
        operations=proposal.operations,
        changes=[DashboardChangeOut(**c) for c in proposal.changes],
        base_version=proposal.base_version,
        base_hash=proposal.base_hash,
        result_spec=proposal.result_spec,
        result_source=store.export(loaded.spec),
        result_hash=proposal.result_hash,
        managed=row.managed,
        warnings=_diagnostics(loaded.warnings),
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
        decided_at=proposal.decided_at,
    )


def _proposal_refused(code: str, message: str, **extra) -> HTTPException:
    status_code = status.HTTP_410_GONE if code == "proposal_expired" else status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code, detail={"error": code, "detail": message, **extra}
    )


async def _proposal(
    db: AsyncSession, ctx: _Context, proposal_id: uuid.UUID
) -> tuple[DashboardProposal, Dashboard]:
    """The caller's own proposal and its dashboard. Anyone else's is a 404."""
    found = (
        await db.execute(
            select(DashboardProposal, Dashboard)
            .join(Dashboard, Dashboard.id == DashboardProposal.dashboard_id)
            .where(
                DashboardProposal.id == proposal_id,
                DashboardProposal.workspace_id == ctx.workspace.id,
                DashboardProposal.created_by == ctx.user.id,
            )
        )
    ).one_or_none()
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No proposal {proposal_id}. A proposal expires a day after it was made.",
        )
    return found[0], found[1]


async def _new_problems(
    db: AsyncSession, ctx: _Context, base: loader.Loaded, result: loader.Loaded
) -> list[loader.Diagnostic]:
    """What the workspace finds wrong with ``result`` that it did not with ``base``."""
    today = today_in(ctx.workspace.timezone)
    found = []
    for loaded in (base, result):
        found.append(
            await validate.problems(
                db,
                loaded,
                workspace_id=ctx.workspace.id,
                user_id=ctx.user.id,
                catalogs=ctx.catalogs,
                today=today,
            )
        )
    known = {d.message for d in found[0]}
    return [d for d in found[1] if d.message not in known]


@router.post(
    "/workspaces/{workspace}/dashboard-proposals",
    status_code=201,
    response_model=DashboardProposalOut,
)
async def create_dashboard_proposal(
    body: DashboardProposalIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardProposalOut:
    """Propose changes to a dashboard, for you to accept as its draft or discard.

    The operations apply in order to the dashboard's newest file, its draft when
    it has one, and nothing changes until the proposal is accepted. An operation
    that cannot apply, such as one naming a widget the dashboard does not have,
    is a 422 `invalid_proposal`. The file they make is checked as publishing
    checks one: a problem it adds, such as a metric that does not exist, is a
    422 `invalid_dashboard` listing every problem. One the dashboard had already
    does not stop a proposal. A dashboard managed in git takes proposals, but
    they cannot be accepted here. Only you can read or decide a proposal, and it
    expires after a day. One the assistant makes is the person's it acted for,
    and the assistant cannot read or decide it."""
    row = await _dashboard(db, ctx, body.dashboard)
    newest, newest_text, _ = _shown(row, draft=True)
    base = _loaded(newest, newest_text)
    try:
        data = proposals.apply(store.stored(base.spec), body.operations)
    except proposals.ProposalInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_proposal", "detail": str(exc), "operation": exc.index},
        ) from exc
    try:
        result = loader.parse(data)
    except loader.DashboardInvalid as exc:
        raise _invalid(exc.code, exc.diagnostics) from exc
    changes = store.changes(base.spec, result.spec)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_proposal", "detail": "The operations change nothing."},
        )
    if problems := await _new_problems(db, ctx, base, result):
        raise _invalid("invalid_dashboard", problems)
    now = datetime.now(UTC)
    await db.execute(delete(DashboardProposal).where(DashboardProposal.expires_at <= now))
    proposal = DashboardProposal(
        workspace_id=ctx.workspace.id,
        dashboard_id=row.id,
        # The assistant proposes with a token for its own account, acting for
        # the person who asked; the proposal is that person's to decide.
        created_by=delegation.person_for(db, ctx.user.id),
        summary=body.summary,
        operations=[op.model_dump(mode="json", exclude_none=True) for op in body.operations],
        base_version=row.version,
        base_hash=store.spec_hash(base.spec),
        result_spec=store.stored(result.spec),
        result_hash=store.spec_hash(result.spec),
        changes=[{"path": path, "change": change} for path, change in changes],
        status="pending",
        created_at=now,
        expires_at=now + PROPOSAL_TTL,
    )
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return _proposal_out(proposal, row)


@router.get(
    "/workspaces/{workspace}/dashboard-proposals/{dashboard_proposal_id}",
    response_model=DashboardProposalOut,
)
async def get_dashboard_proposal(
    dashboard_proposal_id: uuid.UUID,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardProposalOut:
    """Your proposal: what it changes, the file it makes, and whether it can be accepted."""
    proposal, row = await _proposal(db, ctx, dashboard_proposal_id)
    return _proposal_out(proposal, row)


@router.post(
    "/workspaces/{workspace}/dashboard-proposals/{dashboard_proposal_id}/accept",
    response_model=DashboardOut,
)
async def accept_dashboard_proposal(
    dashboard_proposal_id: uuid.UUID,
    request: Request,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardOut:
    """Save your proposal's file as the dashboard's draft. Readers keep the published file.

    The draft is written as canonical YAML, so comments in the draft it
    replaces are not kept. A proposal already accepted or discarded is a 409
    `proposal_decided`, and one a day old a 410 `proposal_expired`. One made
    before the dashboard last changed is a 409 `proposal_stale`, with the
    current draft under `details.current`, so a newer edit is never
    overwritten. A dashboard managed in git is a 409 `managed_dashboard`."""
    proposal, row = await _proposal(db, ctx, dashboard_proposal_id)
    state = _proposal_status(proposal, row)
    if state in ("accepted", "discarded"):
        raise _proposal_refused("proposal_decided", f"This proposal was {state}.")
    if state == "expired":
        raise _proposal_refused(
            "proposal_expired", "This proposal expired a day after it was made."
        )
    store.assert_writable(row)
    if state == "stale":
        raise _proposal_refused(
            "proposal_stale",
            f"{row.name!r} changed after this proposal was made, at version "
            f"{proposal.base_version}; it is at version {row.version}. Ask for the "
            "change again.",
            current=(await _named(db, _out(row, draft=True))).model_dump(mode="json"),
        )
    spec = store.read(proposal.result_spec)
    await _save_draft(db, row, spec, store.export(spec), ctx.user, request)
    row.updated_via = "assistant"
    proposal.status, proposal.decided_at = "accepted", datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await _named(db, _out(row, draft=True))


@router.delete(
    "/workspaces/{workspace}/dashboard-proposals/{dashboard_proposal_id}", status_code=204
)
async def discard_dashboard_proposal(
    dashboard_proposal_id: uuid.UUID,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Discard your proposal. The dashboard is left as it is.

    A proposal already accepted is a 409 `proposal_decided`; discarding one
    twice changes nothing."""
    proposal, _ = await _proposal(db, ctx, dashboard_proposal_id)
    if proposal.status == "accepted":
        raise _proposal_refused("proposal_decided", "This proposal was accepted.")
    if proposal.status == "pending":
        proposal.status, proposal.decided_at = "discarded", datetime.now(UTC)
        await db.commit()


# ── Validation ────────────────────────────────────────────────────────────────


@router.post("/workspaces/{workspace}/dashboard-validations", response_model=DashboardValidationOut)
async def validate_dashboard(
    body: DashboardIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DashboardValidationOut:
    """Check a dashboard file the way publishing does, without storing anything.

    `valid` says whether it could be published as it stands. `errors` and
    `warnings` each have a path into the file, and a line and column when it
    was sent as YAML."""
    try:
        loaded = _parse(body)
    except loader.DashboardInvalid as exc:
        return DashboardValidationOut(
            valid=False, errors=_diagnostics(exc.diagnostics), warnings=[], refs=[]
        )
    problems = await validate.problems(
        db,
        loaded,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        catalogs=ctx.catalogs,
        today=today_in(ctx.workspace.timezone),
    )
    return DashboardValidationOut(
        valid=not problems,
        errors=_diagnostics(problems),
        warnings=_diagnostics(loaded.warnings),
        refs=_refs(loaded.spec),
    )
