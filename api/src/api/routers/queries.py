import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from fastapi import Query as QueryParam
from opentelemetry import trace
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.config import settings
from api.deps import get_current_user, get_db
from api.models.agent import Agent
from api.models.query import Query, SavedQuery
from api.models.user import User
from api.models.worksheet import Worksheet
from api.schemas.page import Page
from api.schemas.query import (
    QueryCreate,
    QueryOut,
    RowsPageOut,
    SavedQueryCreate,
    SavedQueryOut,
    SavedQueryUpdate,
    SqlMetadataOut,
)
from api.services import query as query_service
from api.services import query_history
from api.services import sql_metadata as sql_metadata_service
from api.services.agent_access import assert_agent_tier, assert_can_assign_agent
from api.services.agent_capabilities import agent_supports_catalog, missing_extension
from api.services.agent_dispatch import is_agent_connected, send_to_agent
from api.services.compute import service as compute_service
from api.services.grants import GrantDenied
from api.services.migration.service import workspace_has_active_migration
from api.services.paging import paginate
from api.services.permissions import Permission
from api.services.rbac import has_permission
from api.services.sql_classify import STATEMENT_TYPES
from api.services.sql_guard import SQLNotAllowed, assert_allowed, is_read_only
from api.services.sql_sessions import service as sql_session_service
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)
from duckhaven_shared.concurrency import parse_set_concurrency
from duckhaven_shared.protocol import Frame, FrameType

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/workspaces/{workspace}/queries", status_code=202, response_model=QueryOut)
async def create_query(
    ws: Annotated[str, Path(alias="workspace")],
    body: QueryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
    """Submit SQL for execution. Accepted, not completed: 202 with a query id.

    Dispatch picks a connected agent the caller may use, so 503 means no
    compatible compute is available rather than that the SQL was wrong. Poll
    `GET /queries/{query_id}` for status and read results from
    `GET /queries/{query_id}/rows`."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    # `SET duckhaven_concurrency = '<profile>'` is a DuckHaven control command,
    # not DuckDB SQL (sql_guard rejects SET): intercept it before the guard,
    # retune the selected agent's admission, and record a done query for audit.
    try:
        profile = parse_set_concurrency(body.sql)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "sql_not_allowed", "detail": str(exc)},
        ) from exc
    if profile is not None:
        return await _set_concurrency(db, workspace.id, user, body, profile)

    try:
        assert_allowed(body.sql)
    except SQLNotAllowed as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "sql_not_allowed", "detail": str(exc)},
        ) from exc

    # Reject writes while a catalog attached to this workspace is mid storage
    # migration (the read-only window). Reads are unaffected.
    if not is_read_only(body.sql) and await workspace_has_active_migration(db, workspace.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "catalog_read_only",
                "detail": "A storage backend migration is in progress; the catalog is read-only.",
            },
        )

    # Elastic-pool target: no specific agent chosen. Dispatch to a compatible
    # connected agent if one exists, else park the run queued and provision one.
    if body.agent_id is None:
        if not settings.elastic_compute_enabled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": "agent_required", "detail": "agent_id is required"},
            )
        return await _create_elastic_query(db, workspace, user.id, body)

    result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    # Before the connectivity probe, so a caller without access learns nothing about
    # the agent's state (and an invisible agent 404s exactly like a missing one).
    await assert_agent_tier(db, user, agent, "use")
    if not await is_agent_connected(db, body.agent_id):
        if (
            settings.elastic_compute_enabled
            and agent.provider is not None
            and agent.lifecycle in ("terminated", "failed")
        ):
            return await _create_starting_query(db, workspace, user.id, body, agent)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )

    # Every catalog bound to the workspace is attached on each query, so the
    # agent must support every catalog's kind and storage backend kind.
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    for catalog in catalogs:
        kind = catalog.storage_backend.kind
        if not agent_supports_catalog(agent.capabilities, catalog.kind, kind):
            missing = missing_extension(agent.capabilities, catalog.kind, kind)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "agent_incompatible",
                    "detail": (
                        f"Agent '{agent.name}' is missing the '{missing}' extension required "
                        f"by catalog '{catalog.slug}' ({catalog.kind} on {kind})."
                    ),
                },
            )

    await _stamp_saved_query_run(db, workspace, body.saved_query_id)

    query = Query(
        workspace_id=workspace.id,
        agent_id=body.agent_id,
        user_id=user.id,
        sql=body.sql,
    )
    db.add(query)
    await db.flush()
    try:
        await query_service.dispatch_query(
            db,
            query,
            timeout_s=body.timeout_s,
            active_catalog=body.catalog,
        )
    except GrantDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "grant_denied", "detail": str(exc)},
        ) from exc
    return query


async def _stamp_saved_query_run(db: AsyncSession, workspace, saved_query_id) -> None:
    """Record that a saved query was just run.

    Shared by both create paths. The elastic path did not do this, so a saved
    query only ever run against the pool reported "never run" in the UI while its
    runs sat in History. A missing or foreign id is ignored, so a run never fails
    over a saved query someone deleted.
    """
    if saved_query_id is None:
        return
    saved = (
        await db.execute(
            select(SavedQuery).where(
                SavedQuery.id == saved_query_id,
                SavedQuery.workspace_id == workspace.id,
            )
        )
    ).scalar_one_or_none()
    if saved is not None:
        saved.last_run_at = datetime.now(UTC)


async def _create_starting_query(
    db: AsyncSession, workspace, user_id: uuid.UUID, body: QueryCreate, agent: Agent
) -> Query:
    """Park a run for a named elastic agent and start that agent.

    The mirror of ``_create_elastic_query`` for an explicit target. The run cannot
    be re-routed to whichever pool agent happens to be up — the caller chose this
    one — so it parks ``queued`` with ``agent_id`` NULL and ``requested_agent_id``
    set, and ``compute.service.bind_targeted_work`` dispatches it when the agent
    dials home. Failing instead would make an idle-terminated agent permanently
    unusable, because the reaper tears it down precisely *because* nothing is using
    it — the reasoning the scheduler already applies to an unattended run.

    ``origin`` stays null: this is an interactive run, and History must not report
    it as anything else. Compatibility is checked when the agent registers, not
    here: a row that failed while provisioning advertises no capabilities at all.
    """
    await _stamp_saved_query_run(db, workspace, body.saved_query_id)
    query = Query(
        workspace_id=workspace.id,
        agent_id=None,
        requested_agent_id=agent.id,
        user_id=user_id,
        sql=body.sql,
        status="queued",
        # Recorded for the same reason as a parked pool run: the dispatch happens
        # outside this request and would otherwise fall back to the workspace
        # default catalog and timeout.
        timeout_s=body.timeout_s,
        active_catalog=body.catalog,
    )
    db.add(query)
    # Commit before provisioning: on Docker the agent can register within a second,
    # and the binder can only claim a row it can see.
    await db.commit()

    if await compute_service.restart_elastic_agent(db, agent) is None:
        query.status = "failed"
        query.error = "Could not start the configured agent."
        query.finished_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "compute_unavailable",
                "detail": "Could not start the configured agent.",
            },
            headers={"Retry-After": "5"},
        )
    logger.info("Query %s starting terminated agent %s", query.id, agent.id)
    return query


async def _create_elastic_query(
    db: AsyncSession, workspace, user_id: uuid.UUID, body: QueryCreate
) -> Query:
    """Run against the elastic pool: dispatch now if a compatible agent is up,
    otherwise park the run ``queued`` and provision one (bound on registration)."""
    await _stamp_saved_query_run(db, workspace, body.saved_query_id)
    # Scoped to agents the caller may use, or omitting `agent_id` would be a way
    # around a denial on a specific agent.
    agent = await query_service.pick_agent_for(db, workspace, principal_id=user_id)
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id if agent is not None else None,
        user_id=user_id,
        sql=body.sql,
        status="queued",
        origin="elastic",
        # Recorded because this run may be dispatched long after this request: a run
        # parked during a cold start is replayed by compute.service.bind_queued_work,
        # which has no access to the request that created it. Without these the replay
        # used the workspace default catalog and the default timeout, so unqualified
        # table names resolved somewhere the user had not chosen.
        timeout_s=body.timeout_s,
        active_catalog=body.catalog,
    )
    db.add(query)
    await db.flush()

    if agent is not None:
        try:
            await query_service.dispatch_query(
                db, query, timeout_s=body.timeout_s, active_catalog=body.catalog
            )
        except GrantDenied as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "grant_denied", "detail": str(exc)},
            ) from exc
        except query_service.AgentUnavailable:
            # Presence is read from Postgres with a TTL, so the agent picked above can
            # have lost its socket already -- and a terminating agent keeps its
            # ownership row until the container actually goes away. For a pool run that
            # is not a failure: unbind and fall through to scale-out, exactly as if no
            # agent had been available. Raising here would surface as a 500.
            logger.info("Elastic pool agent %s was unavailable; provisioning", agent.id)
            query.agent_id = None
        else:
            return query

    # No compatible agent connected: coalesced scale-out. The run stays queued
    # (agent_id NULL) until the provisioned agent registers and binds it.
    pool_key = await compute_service.resolve_pool_key(db, workspace)
    await compute_service.ensure_agent(db, pool_key)
    await db.commit()
    return query


async def _set_concurrency(
    db: AsyncSession, workspace_id: uuid.UUID, user: User, body: QueryCreate, profile: str
) -> Query:
    """Apply a concurrency `SET` to the selected agent and log it as a done query.

    Agent-global: it retunes admission for every query on that agent, not just
    this user's. The agent owns the profile (held in memory, reset on restart).

    That fleet-wide blast radius is why this needs `operate` rather than `use`:
    retuning admission changes how the agent serves everyone on it, which is a
    lifecycle-grade act, not a dispatch.
    """
    result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    await assert_agent_tier(db, user, agent, "operate")
    if not await is_agent_connected(db, body.agent_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )
    frame = Frame(type=FrameType.SET_CONCURRENCY, payload={"profile": profile})
    await send_to_agent(db, body.agent_id, frame.model_dump_json())
    query = Query(
        workspace_id=workspace_id,
        agent_id=body.agent_id,
        user_id=user.id,
        sql=body.sql,
        status="done",
        row_count=0,
        finished_at=datetime.now(tz=UTC),
    )
    db.add(query)
    await db.commit()
    await db.refresh(query)
    return query


@router.get("/workspaces/{workspace}/sql-metadata", response_model=SqlMetadataOut)
async def get_sql_metadata(
    ws: Annotated[str, Path(alias="workspace")],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SqlMetadataOut:
    """DuckDB function/keyword/type dictionary for editor autocomplete.

    Sourced live from a connected agent (cached per DuckDB version). Returns 503
    when no compatible agent is connected so the editor falls back to its static
    keyword list rather than caching an empty dictionary.
    """
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    agent = await query_service.pick_agent_for(db, workspace, principal_id=user.id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No compatible agent is connected.",
        )
    try:
        return await sql_metadata_service.fetch_metadata(db, workspace, agent, user.id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Could not load SQL metadata from the agent.",
        ) from exc


@router.get("/workspaces/{workspace}/queries", response_model=Page[QueryOut])
async def list_workspace_queries(
    ws: Annotated[str, Path(alias="workspace")],
    all_workspaces: bool = QueryParam(default=False),
    user_id: uuid.UUID | None = QueryParam(default=None),
    agent_id: uuid.UUID | None = QueryParam(default=None),
    since: datetime | None = QueryParam(default=None),
    until: datetime | None = QueryParam(default=None),
    origin: str | None = QueryParam(default=None),
    session_id: uuid.UUID | None = QueryParam(default=None),
    q: str | None = QueryParam(default=None),
    query_id: str | None = QueryParam(default=None),
    status_in: list[str] | None = QueryParam(default=None, alias="status"),
    statement_type: list[str] | None = QueryParam(default=None),
    slower_than_ms: int | None = QueryParam(default=None, ge=0),
    sort: query_history.SortKey = QueryParam(default="started_at"),
    dir: query_history.SortDir = QueryParam(default="desc"),
    cursor: str | None = QueryParam(default=None),
    limit: int = QueryParam(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[QueryOut]:
    """Query log, newest first. Doubles as the admin audit trail.

    Paged with a keyset cursor rather than an offset, because history is written
    to continuously and an offset page would duplicate and skip rows under load
    (see :mod:`api.services.query_history`). Sorting and filtering happen here,
    over the whole result set, before the page is cut — a client that sorted the
    page it was handed would be sorting a hundred rows out of thousands.

    A member sees their own workspace, gated on membership, and may narrow it
    however they like: by agent, kind of run, session, statement text, id,
    status, duration, statement type, and time. None of those reveal a row the
    list would not already have shown them.

    The cross-principal filters stay admin-only: ``all_workspaces``, and a
    ``user_id`` other than the caller's own. Filtering to *yourself* is not
    cross-principal, so it is open to anyone — it is how the default History
    view scopes itself.
    """
    is_admin = await has_permission(db, user, Permission.QUERIES_ADMIN)

    # Cross-workspace first: this branch skips the membership check below, so
    # nothing that follows may run before it has been refused.
    if all_workspaces:
        if not is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        workspace = None
    else:
        workspace = await get_workspace(db, ws)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        await assert_workspace_member(db, workspace.id, user.id)

    if user_id is not None and user_id != user.id and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if status_in is not None:
        unknown = sorted(set(status_in) - query_history.QUERY_STATUSES)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown status: {', '.join(unknown)}",
            )
    if statement_type is not None:
        unknown = sorted(set(statement_type) - STATEMENT_TYPES)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown statement type: {', '.join(unknown)}",
            )

    # The sort key is selected alongside the row so the cursor is built from the
    # value the database computed, not one recomputed in Python.
    sort_value = query_history.duration_expr() if sort == "duration" else Query.started_at

    # Left-join the user so History can show who ran each query (the name is
    # attached to each Query row below for QueryOut serialization).
    stmt = (
        select(Query, User.name, sort_value)
        .outerjoin(User, Query.user_id == User.id)
        .where(
            or_(
                Query.origin.is_(None),
                Query.origin.notin_(query_history.HIDDEN_ORIGINS),
            )
        )
        .order_by(*query_history.order_by(sort, dir))
    )

    if workspace is not None:
        stmt = stmt.where(Query.workspace_id == workspace.id)

    if origin is not None:
        # Interactive runs are stored with a null origin (the column only tags
        # non-interactive ones), so the filter spells that case explicitly rather
        # than leaving the UI unable to ask for it.
        stmt = stmt.where(
            Query.origin.is_(None) if origin == "interactive" else Query.origin == origin
        )
    if session_id is not None:
        stmt = stmt.where(Query.session_id == session_id)
    if agent_id is not None:
        # Open to any workspace member. By this point the statement is already
        # scoped to their own workspace (all_workspaces is admin-only and 403s
        # above), so this reveals nothing a member could not already see.
        stmt = stmt.where(Query.agent_id == agent_id)
    if user_id is not None:
        stmt = stmt.where(Query.user_id == user_id)
    if since is not None:
        stmt = stmt.where(Query.started_at >= since)
    if until is not None:
        stmt = stmt.where(Query.started_at <= until)
    if q:
        stmt = stmt.where(query_history.search_predicate(q))
    if query_id:
        try:
            # Composed as one more predicate, never as a short-circuit: knowing
            # an id must not be a way around the workspace scoping above.
            stmt = stmt.where(query_history.id_predicate(query_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
    if status_in:
        stmt = stmt.where(Query.status.in_(status_in))
    if statement_type:
        # Rows classified as unknown (null) drop out here, and only here: with
        # the filter off they stay visible, because "we never classified this"
        # is not the same as "this is not a SELECT".
        stmt = stmt.where(Query.statement_type.in_(statement_type))
    if slower_than_ms is not None:
        stmt = stmt.where(query_history.duration_expr() >= slower_than_ms)

    if cursor:
        try:
            anchor_value, anchor_id = query_history.decode_cursor(cursor, sort)
        except query_history.InvalidCursor as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        stmt = stmt.where(query_history.keyset_predicate(sort, dir, anchor_value, anchor_id))

    # One more than asked for: its presence is what `has_more` reports, and it
    # costs a row rather than the COUNT(*) a total would.
    result = await db.execute(stmt.limit(limit + 1))
    rows = result.all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    queries: list[Query] = []
    for query, user_name, _value in rows:
        query.user_name = user_name
        queries.append(query)
    next_cursor = (
        query_history.encode_cursor(rows[-1][0].id, rows[-1][2]) if has_more and rows else None
    )
    return Page[QueryOut](items=queries, cursor=next_cursor, has_more=has_more)


@router.get("/queries/{query_id}", response_model=QueryOut)
async def get_query(
    query_id: uuid.UUID,
    wait_timeout_s: float = QueryParam(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
    """One query's status, timings and error, by its global id.

    Addressed globally rather than under its workspace: the id is unique, and the
    submitter already holds it. Any member of the query's workspace may read it,
    which is what makes the history an audit trail.

    Pass `wait_timeout_s` to hold the response until the query reaches a terminal
    state, for a client following a statement that outlived the wait on submit.
    It is **opt-in and defaults to 0** deliberately: this is also the route the
    worksheet refreshes several times a second and the history reads, and neither
    wants a held request. Capped by `SQL_STATEMENT_MAX_WAIT_TIMEOUT_S`."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    if wait_timeout_s > 0:
        if wait_timeout_s > settings.sql_statement_max_wait_timeout_s:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"wait_timeout_s must be at most {settings.sql_statement_max_wait_timeout_s}"
                ),
            )
        await sql_session_service.await_query_done(db, query, wait_timeout_s)
    return query


@router.get("/queries/{query_id}/profile")
async def get_query_profile(
    query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict | None:
    """The normalized post-execution profile, or null when none was captured.

    Kept off ``QueryOut`` so list/history stay lean; the worksheet fetches it on
    demand when a query is done.
    """
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    return query.profile


@router.delete("/queries/{query_id}", status_code=204)
async def cancel_query(
    query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Ask the agent to stop a running query.

    Best effort and idempotent: cancelling a query that already finished is a
    no-op, not an error. The query row survives -- cancellation is a terminal
    status, not a deletion."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    await query_service.cancel_query(db, query)


@router.get("/queries/{query_id}/rows", response_model=RowsPageOut)
async def get_query_rows(
    query_id: uuid.UUID,
    # Capped like every other paged endpoint: uncapped, one request could ask
    # for the whole result set and defeat the paging this route exists to do.
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RowsPageOut:
    """A page of a finished query's result rows, with their column types.

    A result grid rather than a resource collection, which is why the envelope is
    `rows`/`columns` rather than `items`. Page with the opaque `cursor`."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    if query.status != "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Query not done")
    # DDL/DML statements complete without a result file. Report an empty page
    # rather than a 404 so the UI shows a clean "ran, no rows" state.
    if query.result_path is None:
        return RowsPageOut(rows=[], columns=[], cursor=None, total=0, column_schema=None)
    if query.agent_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No results available")

    trace.get_current_span().set_attribute(
        "duckhaven.result_schema", query.result_schema is not None
    )
    return await query_service.fetch_result_page(
        db, query, limit=limit, offset=int(cursor) if cursor and cursor.isdigit() else 0
    )


@router.get("/workspaces/{workspace}/saved-queries", response_model=Page[SavedQueryOut])
async def list_saved_queries(
    ws: Annotated[str, Path(alias="workspace")],
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[SavedQueryOut]:
    """The workspace's saved queries, newest first, with who saved and last edited each.

    Shared, not per-user: a saved query belongs to the workspace, so any member
    sees all of them."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    # Join the creator and the last editor so the list can attribute both.
    editor = aliased(User)
    rows, next_cursor, has_more = await paginate(
        db,
        select(SavedQuery, User.name, editor.name)
        .join(User, SavedQuery.created_by == User.id)
        .join(editor, SavedQuery.updated_by == editor.id)
        .where(SavedQuery.workspace_id == workspace.id),
        sort=[SavedQuery.created_at.desc(), SavedQuery.id.desc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[SavedQueryOut](
        items=[
            SavedQueryOut.model_validate(sq).model_copy(
                update={"created_by_name": name, "updated_by_name": editor_name}
            )
            for sq, name, editor_name in rows
        ],
        cursor=next_cursor,
        has_more=has_more,
    )


async def _saved_query_named(
    db: AsyncSession, workspace_id: uuid.UUID, name: str, *, exclude: uuid.UUID | None = None
) -> SavedQuery | None:
    """The saved query already using `name` in this workspace, ignoring case."""
    stmt = select(SavedQuery).where(
        SavedQuery.workspace_id == workspace_id,
        func.lower(SavedQuery.name) == name.lower(),
    )
    if exclude is not None:
        stmt = stmt.where(SavedQuery.id != exclude)
    return (await db.execute(stmt)).scalar_one_or_none()


def _name_taken(existing: SavedQuery | None, name: str) -> HTTPException:
    detail: dict[str, object] = {
        "error": "saved_query_exists",
        "detail": f"A saved query named '{name}' already exists in this workspace",
    }
    if existing is not None:
        detail.update(id=str(existing.id), name=existing.name)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post(
    "/workspaces/{workspace}/saved-queries",
    status_code=201,
    responses={
        200: {"description": "A saved query with this name existed; its SQL was replaced."},
        201: {"description": "A new saved query was created."},
    },
    response_model=SavedQueryOut,
)
async def create_saved_query(
    ws: Annotated[str, Path(alias="workspace")],
    body: SavedQueryCreate,
    response: Response,
    on_conflict: Literal["replace", "error"] = QueryParam(
        default="replace",
        description=(
            "What to do when a saved query with this name (ignoring case) exists: "
            "`replace` its SQL and default agent, or fail with 409 `saved_query_exists`."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedQuery:
    """Save SQL under a name.

    By default an existing query with the same name (ignoring case) is replaced
    rather than duplicated: 201 when a query was created, 200 when one was
    replaced. Pass `on_conflict=error` to get a 409 instead, so a client can ask
    before overwriting a query someone else shares. Requires `writer`."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    # A default agent is a live dispatch path (the scheduler falls back to it), so
    # it needs the same `use` tier as choosing the agent on a schedule.
    await assert_can_assign_agent(db, user, body.default_agent_id)
    sq = await _saved_query_named(db, workspace.id, body.name)
    if sq is not None:
        if on_conflict == "error":
            raise _name_taken(sq, body.name)
        # Replacing keeps the creator for attribution; the editor becomes the
        # principal scheduled runs execute as.
        sq.name = body.name
        sq.sql = body.sql
        sq.default_agent_id = body.default_agent_id
        sq.updated_by = user.id
        sq.updated_at = datetime.now(UTC)
        response.status_code = status.HTTP_200_OK
    else:
        sq = SavedQuery(
            workspace_id=workspace.id,
            name=body.name,
            sql=body.sql,
            default_agent_id=body.default_agent_id,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(sq)
    try:
        await db.commit()
    except IntegrityError:
        # Another save claimed the name between the lookup and the commit.
        await db.rollback()
        raise _name_taken(None, body.name) from None
    await db.refresh(sq)
    return sq


@router.patch(
    "/workspaces/{workspace}/saved-queries/{saved_query_id}", response_model=SavedQueryOut
)
async def update_saved_query(
    ws: Annotated[str, Path(alias="workspace")],
    saved_query_id: uuid.UUID,
    body: SavedQueryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedQuery:
    """Change a saved query's name, SQL or default agent. Requires `writer`.

    A partial update: omitted fields are left alone. Changing the SQL or the
    default agent makes the caller the query's editor, which is the principal its
    scheduled runs execute as. Renaming onto another query's name is a 409."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == saved_query_id,
            SavedQuery.workspace_id == workspace.id,
        )
    )
    sq = result.scalar_one_or_none()
    if sq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found")
    fields = body.model_dump(exclude_unset=True)
    if "default_agent_id" in fields:
        await assert_can_assign_agent(db, user, fields["default_agent_id"])
    if fields.get("name") is not None:
        clash = await _saved_query_named(db, workspace.id, fields["name"], exclude=sq.id)
        if clash is not None:
            raise _name_taken(clash, fields["name"])
    for key, value in fields.items():
        setattr(sq, key, value)
    if "sql" in fields or "default_agent_id" in fields:
        sq.updated_by = user.id
    sq.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _name_taken(None, fields.get("name") or sq.name) from None
    await db.refresh(sq)
    return sq


@router.delete("/workspaces/{workspace}/saved-queries/{saved_query_id}", status_code=204)
async def delete_saved_query(
    ws: Annotated[str, Path(alias="workspace")],
    saved_query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a saved query. Requires `writer`.

    Schedules that referenced it are not silently orphaned -- see the schedules
    router for how a scheduled saved query is retired."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == saved_query_id,
            SavedQuery.workspace_id == workspace.id,
        )
    )
    sq = result.scalar_one_or_none()
    if sq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found")
    # Worksheets linked to it keep their SQL and become plain drafts. Done here as
    # well as by the FK's ON DELETE SET NULL, which SQLite does not enforce.
    await db.execute(
        update(Worksheet).where(Worksheet.saved_query_id == sq.id).values(saved_query_id=None)
    )
    await db.delete(sq)
    await db.commit()
