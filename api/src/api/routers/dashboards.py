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

import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.deps import _bearer, get_current_user, get_db, get_polaris_client, get_session_factory
from api.models.catalog import Catalog
from api.models.dashboard import Dashboard, DashboardRef, DashboardRevision
from api.models.user import User
from api.schemas.dashboard import (
    DashboardCreate,
    DashboardIn,
    DashboardOut,
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
from api.services.dashboards import loader, store, validate
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
        client=row.client,
        created_by=row.created_by,
        published_by=row.published_by,
        refs=_refs(loaded.spec),
        warnings=_diagnostics(loaded.warnings),
    )


def _stamp(row: Dashboard, user: User, request: Request) -> None:
    """Record who made a change, and how it arrived: the web app, the CLI or the API."""
    name, version = parse_user_agent(request.headers.get("user-agent"))
    client = f"{name}/{version}" if name and version else name
    if _bearer(request.headers.get("authorization")) is None:
        via = "ui"  # a session cookie: the web app
    else:
        via = "cli" if name == "duckhaven-cli" else "rest"
    row.updated_by, row.updated_via, row.client = user.id, via, client[:255] if client else None
    row.updated_at = datetime.now(UTC)


async def _follow(db: AsyncSession, row: Dashboard, spec: DashboardSpec) -> None:
    """Copy name, title, description and labels from the file the listing shows."""
    await store.claim_name(db, row, spec.name)
    row.title, row.description, row.labels = spec.title, spec.description, list(spec.labels)
    row.spec_version = spec.version


def _assert_base(row: Dashboard, base_version: int) -> None:
    if base_version != row.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dashboard_conflict",
                "detail": (
                    f"{row.name!r} changed since version {base_version}; "
                    f"it is at version {row.version}."
                ),
                "current": _out(row, draft=True).model_dump(mode="json"),
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
    metric that does not exist, do not stop a draft; publishing checks them."""
    spec = _read(body).spec
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
    await db.commit()
    await db.refresh(row)
    return _out(row, draft=True)


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
    return _out(await _dashboard(db, ctx, dashboard), draft=draft)


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
    working."""
    row = await _dashboard(db, ctx, dashboard)
    store.assert_writable(row)
    _assert_base(row, body.base_version)
    spec = _read(body).spec
    if row.spec is None:
        await _follow(db, row, spec)
    elif spec.name != row.name:
        await store.assert_name_free(db, row.workspace_id, spec.name, mine=row.id)
    row.draft_spec, row.draft_source_text = store.stored(spec), body.source_text
    row.version += 1
    _stamp(row, ctx.user, request)
    await store.set_refs(db, row, "draft", spec)
    await db.commit()
    await db.refresh(row)
    return _out(row, draft=True)


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
    _assert_base(row, body.base_version)
    if row.draft_spec is None:
        return _out(row)
    loaded = _loaded(row.draft_spec, row.draft_source_text)
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

    spec = loaded.spec
    await _follow(db, row, spec)
    last = (
        await db.execute(
            select(func.max(DashboardRevision.number)).where(
                DashboardRevision.dashboard_id == row.id
            )
        )
    ).scalar_one()
    now = datetime.now(UTC)
    row.spec, row.source_text = store.stored(spec), row.draft_source_text
    row.spec_hash = store.spec_hash(spec)
    row.draft_spec = row.draft_source_text = None
    row.status, row.published_by, row.published_at = "published", ctx.user.id, now
    row.version += 1
    _stamp(row, ctx.user, request)
    db.add(
        DashboardRevision(
            dashboard_id=row.id,
            number=(last or 0) + 1,
            spec=row.spec,
            source_text=row.source_text,
            spec_hash=row.spec_hash,
            message=body.message,
            published_by=ctx.user.id,
            published_at=now,
        )
    )
    await store.set_refs(db, row, "published", spec)
    await store.set_refs(db, row, "draft", None)
    await db.commit()
    await db.refresh(row)
    return _out(row)


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
    try:
        plan = await run_service.plan(
            db,
            workspace=ctx.workspace,
            catalogs=ctx.catalogs,
            user=ctx.user,
            spec=spec,
            dashboard={
                "name": row.name,
                "version": row.version,
                "spec_hash": store.spec_hash(spec),
                "draft": is_draft,
            },
            today=today_in(ctx.workspace.timezone),
            values=body.params,
            widget_ids=body.widgets,
            use_cache=body.use_cache,
            client=parse_user_agent(request.headers.get("user-agent")),
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
