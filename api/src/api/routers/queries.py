import logging
import time
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi import Query as QueryParam
from opentelemetry import trace
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_current_user, get_db
from api.metrics import record_rows_decode
from api.models.agent import Agent
from api.models.query import Query, SavedQuery
from api.models.user import User
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
from api.services import sql_metadata as sql_metadata_service
from api.services.agent_capabilities import agent_supports_backend, required_extension
from api.services.agent_dispatch import is_agent_connected, send_to_agent
from api.services.compute import service as compute_service
from api.services.grants import GrantDenied
from api.services.migration.service import workspace_has_active_migration
from api.services.permissions import Permission
from api.services.rbac import has_permission
from api.services.sql_guard import SQLNotAllowed, assert_allowed, is_read_only
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)
from duckhaven_shared.concurrency import parse_set_concurrency
from duckhaven_shared.protocol import Frame, FrameType

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/workspaces/{ws}/queries", status_code=202, response_model=QueryOut)
async def create_query(
    ws: str,
    body: QueryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
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
        return await _set_concurrency(db, workspace.id, user.id, body, profile)

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
    if not await is_agent_connected(db, body.agent_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )

    # Every catalog bound to the workspace is attached on each query, so the
    # agent must support every backend kind across them.
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    for catalog in catalogs:
        kind = catalog.storage_backend.kind
        if not agent_supports_backend(agent.capabilities, kind):
            ext = required_extension(kind)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "agent_incompatible",
                    "detail": (
                        f"Agent '{agent.name}' is missing the '{ext}' extension required "
                        f"by catalog '{catalog.slug}'s {kind} backend."
                    ),
                },
            )

    # When the run came from a saved query, stamp its last_run_at. Ignore a
    # missing/foreign id so a run never fails over a deleted saved query.
    if body.saved_query_id is not None:
        result = await db.execute(
            select(SavedQuery).where(
                SavedQuery.id == body.saved_query_id,
                SavedQuery.workspace_id == workspace.id,
            )
        )
        saved = result.scalar_one_or_none()
        if saved is not None:
            saved.last_run_at = datetime.now(UTC)

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


async def _create_elastic_query(
    db: AsyncSession, workspace, user_id: uuid.UUID, body: QueryCreate
) -> Query:
    """Run against the elastic pool: dispatch now if a compatible agent is up,
    otherwise park the run ``queued`` and provision one (bound on registration)."""
    agent = await query_service.pick_agent_for(db, workspace)
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
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, body: QueryCreate, profile: str
) -> Query:
    """Apply a concurrency `SET` to the selected agent and log it as a done query.

    Agent-global: it retunes admission for every query on that agent, not just
    this user's. The agent owns the profile (held in memory, reset on restart).
    """
    result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not await is_agent_connected(db, body.agent_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )
    frame = Frame(type=FrameType.SET_CONCURRENCY, payload={"profile": profile})
    await send_to_agent(db, body.agent_id, frame.model_dump_json())
    query = Query(
        workspace_id=workspace_id,
        agent_id=body.agent_id,
        user_id=user_id,
        sql=body.sql,
        status="done",
        row_count=0,
        finished_at=datetime.now(tz=UTC),
    )
    db.add(query)
    await db.commit()
    await db.refresh(query)
    return query


@router.get("/workspaces/{ws}/sql-metadata", response_model=SqlMetadataOut)
async def get_sql_metadata(
    ws: str,
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

    agent = await query_service.pick_agent_for(db, workspace)
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


@router.get("/workspaces/{ws}/queries", response_model=list[QueryOut])
async def list_workspace_queries(
    ws: str,
    all_workspaces: bool = QueryParam(default=False),
    user_id: uuid.UUID | None = QueryParam(default=None),
    agent_id: uuid.UUID | None = QueryParam(default=None),
    since: datetime | None = QueryParam(default=None),
    until: datetime | None = QueryParam(default=None),
    origin: str | None = QueryParam(default=None),
    session_id: uuid.UUID | None = QueryParam(default=None),
    limit: int = QueryParam(default=100, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Query]:
    """Query log, newest first. Doubles as the admin audit trail.

    A member sees their workspace (gated on membership). An admin can pass
    ``all_workspaces`` to see every workspace, and the ``user_id``/``agent_id``/
    ``since``/``until`` filters; those affordances are admin-only and rejected
    with 403 for non-admins. Internal queries (e.g. table-sample previews) are
    excluded.

    ``origin`` and ``session_id`` narrow to a kind of run (``"session"``,
    ``"scheduled"``) or to one session's statements. They reveal nothing a member
    could not already see in this list, so they are open to any member — unlike
    the cross-principal filters above.
    """
    is_admin = await has_permission(db, user, Permission.QUERIES_ADMIN)

    # Left-join the user so History can show who ran each query (the name is
    # attached to each Query row below for QueryOut serialization).
    stmt = (
        select(Query, User.name)
        .outerjoin(User, Query.user_id == User.id)
        .where(or_(Query.origin.is_(None), Query.origin.notin_(("sample", "metadata"))))
        .order_by(Query.started_at.desc())
        .limit(limit)
    )

    if all_workspaces:
        if not is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    else:
        workspace = await get_workspace(db, ws)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        await assert_workspace_member(db, workspace.id, user.id)
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

    if any(f is not None for f in (user_id, agent_id, since, until)):
        if not is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        if user_id:
            stmt = stmt.where(Query.user_id == user_id)
        if agent_id:
            stmt = stmt.where(Query.agent_id == agent_id)
        if since:
            stmt = stmt.where(Query.started_at >= since)
        if until:
            stmt = stmt.where(Query.started_at <= until)

    result = await db.execute(stmt)
    queries: list[Query] = []
    for query, user_name in result.all():
        query.user_name = user_name
        queries.append(query)
    return queries


@router.get("/queries/{query_id}", response_model=QueryOut)
async def get_query(
    query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
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
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    await query_service.cancel_query(db, query)


@router.get("/queries/{query_id}/rows", response_model=RowsPageOut)
async def get_query_rows(
    query_id: uuid.UUID,
    limit: int = 100,
    cursor: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RowsPageOut:
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

    agent_result = await db.execute(select(Agent).where(Agent.id == query.agent_id))
    agent = agent_result.scalar_one_or_none()

    # An elastic agent's address is assigned after its instance is created, so it can be
    # unknown at registration time. Resolve it on first use, when the cloud is certain
    # to be able to answer.
    if agent is not None and agent.provider is not None and agent.result_host is None:
        from api.services.compute.service import ensure_result_host

        await ensure_result_host(db, agent)

    if agent is None or agent.result_host is None or agent.result_port is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent result endpoint unavailable",
        )

    token = await query_service.agent_session_token(db, query.agent_id)

    offset = int(cursor) if cursor and cursor.isdigit() else 0
    try:
        upstream = await query_service.proxy_rows(
            agent, query, row_offset=offset, row_limit=limit, token=token
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent result endpoint unavailable",
        ) from exc
    if upstream.status_code == 404:
        # The result file was swept by retention while the user was paging.
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Result no longer available")
    if upstream.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch results from agent"
        )
    # When the agent sliced the window (X-DH-Row-Offset present) the body already
    # starts at the requested rows, so decode at offset 0. An older agent that
    # ignored the params returns the whole file — fall back to decoding at offset.
    decode_offset = 0 if "X-DH-Row-Offset" in upstream.headers else offset
    started = time.monotonic()
    rows, columns = query_service.decode_parquet_page(upstream.content, limit, decode_offset)
    record_rows_decode(time.monotonic() - started)
    trace.get_current_span().set_attribute(
        "duckhaven.result_schema", query.result_schema is not None
    )
    total = query.row_count or 0
    next_offset = offset + limit
    next_cursor = str(next_offset) if next_offset < total else None
    return RowsPageOut(
        rows=rows,
        columns=columns,
        cursor=next_cursor,
        total=total,
        column_schema=query.result_schema,
    )


@router.get("/workspaces/{ws}/saved-queries", response_model=list[SavedQueryOut])
async def list_saved_queries(
    ws: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SavedQueryOut]:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    # Join the creator so the list can show who saved each query (attribution).
    result = await db.execute(
        select(SavedQuery, User.name)
        .join(User, SavedQuery.created_by == User.id)
        .where(SavedQuery.workspace_id == workspace.id)
    )
    return [
        SavedQueryOut.model_validate(sq).model_copy(update={"created_by_name": name})
        for sq, name in result.all()
    ]


@router.post(
    "/workspaces/{ws}/saved-queries",
    status_code=201,
    response_model=SavedQueryOut,
)
async def create_saved_query(
    ws: str,
    body: SavedQueryCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedQuery:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    # Overwrite by name: saving over an existing name updates that query instead
    # of creating a duplicate ("report v1", "report v2", ...).
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.workspace_id == workspace.id,
            SavedQuery.name == body.name,
        )
    )
    sq = result.scalar_one_or_none()
    if sq is not None:
        sq.sql = body.sql
        sq.default_agent_id = body.default_agent_id
        response.status_code = status.HTTP_200_OK
    else:
        sq = SavedQuery(
            workspace_id=workspace.id,
            name=body.name,
            sql=body.sql,
            default_agent_id=body.default_agent_id,
            created_by=user.id,
        )
        db.add(sq)
    await db.commit()
    await db.refresh(sq)
    return sq


@router.patch("/workspaces/{ws}/saved-queries/{sq_id}", response_model=SavedQueryOut)
async def update_saved_query(
    ws: str,
    sq_id: uuid.UUID,
    body: SavedQueryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedQuery:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == sq_id,
            SavedQuery.workspace_id == workspace.id,
        )
    )
    sq = result.scalar_one_or_none()
    if sq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found")
    fields = body.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(sq, key, value)
    await db.commit()
    await db.refresh(sq)
    return sq


@router.delete("/workspaces/{ws}/saved-queries/{sq_id}", status_code=204)
async def delete_saved_query(
    ws: str,
    sq_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == sq_id,
            SavedQuery.workspace_id == workspace.id,
        )
    )
    sq = result.scalar_one_or_none()
    if sq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found")
    await db.delete(sq)
    await db.commit()
