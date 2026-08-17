"""SQL session endpoints (the dbt/dlt front door).

A client opens a session (bound to one agent), runs many statements against its
persistent DuckDB connection, then closes it. Every call is a PAT/session
authenticated API call (I10) that the API brokers to the agent over the
agent-initiated socket (I2). The whole surface is gated on
``settings.sql_sessions_enabled`` (off by default) so the relaxed statement policy
is only reachable on a deployment the operator has hardened.

Statements are ordinary ``queries`` rows (``origin="session"``); poll and fetch
them through the existing ``GET /queries/{id}`` (+ ``/rows``).
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi import Query as QueryParam
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_current_user, get_db
from api.metrics import record_sql_session_closed, record_statement_policy_rejection
from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.user import User
from api.schemas.query import QueryOut
from api.schemas.sql_session import (
    SqlSessionCreate,
    SqlSessionOut,
    SqlSessionSummaryOut,
    SqlStatementCreate,
    StagedFileOut,
    StagingFilesCreate,
    StagingFilesOut,
)
from api.services import session_credentials, staging_presign
from api.services import statement_policy as policy
from api.services.agent_access import assert_agent_tier
from api.services.agent_capabilities import agent_supports_backend, required_extension
from api.services.agent_dispatch import is_agent_connected
from api.services.compute import service as compute_service
from api.services.grants import GrantDenied, assert_query_access
from api.services.migration.service import workspace_has_active_migration
from api.services.permissions import Permission
from api.services.query import pick_agent_for
from api.services.rbac import has_permission
from api.services.sql_guard import is_read_only
from api.services.sql_sessions import service as session_service
from api.services.sql_sessions.client_info import parse_user_agent
from api.services.workspace import (
    assert_workspace_member,
    get_default_catalog,
    get_workspace,
    resolve_catalog,
    resolve_workspace_catalogs,
)

router = APIRouter()


def _require_enabled() -> None:
    if not settings.sql_sessions_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SQL sessions are not enabled"
        )


async def _load_session(db: AsyncSession, session_id: uuid.UUID, user: User) -> SqlSession:
    session = await db.get(SqlSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await assert_workspace_member(db, session.workspace_id, user.id)
    return session


def _is_restartable_elastic(agent: Agent) -> bool:
    """Whether sending work to this agent should start it rather than fail.

    Only an elastic agent the reaper (or a failed provision) already took down:
    a static agent has nothing to start, and one still ``running`` that merely
    lost its socket is a connectivity problem, not a cold pool.
    """
    return (
        settings.elastic_compute_enabled
        and agent.provider is not None
        and agent.lifecycle in ("terminated", "failed")
    )


async def _start_compute(
    db: AsyncSession, session: SqlSession, workspace, restarting: Agent | None
) -> bool:
    """Bring up compute for a session parked ``pending``. False if it cannot start.

    Called after the session row is committed, so whichever agent comes up can find
    and claim it. ``ensure_agent`` returning None is *not* a failure: it means the
    per-pool cap is already held by a row that is provisioning, and that agent will
    bind this session when it registers.
    """
    if restarting is not None:
        if await compute_service.restart_elastic_agent(db, restarting) is None:
            session.status = "failed"
            session.error = "compute_unavailable"
            session.close_reason = "failed"
            session.closed_at = datetime.now(tz=UTC)
            await db.commit()
            return False
        return True
    pool_key = await compute_service.resolve_pool_key(db, workspace)
    await compute_service.ensure_agent(db, pool_key)
    await db.commit()
    return True


@router.post("/workspaces/{ws}/sql/sessions", status_code=201, response_model=SqlSessionOut)
async def open_session(
    ws: str,
    body: SqlSessionCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SqlSession:
    """Open a session, starting compute first when none is up.

    Synchronous by contract — it returns a session a client can immediately run
    statements on — so when compute has to start it holds the request rather than
    parking silently. `wait_timeout_s`/`on_wait_timeout` choose how long and what
    happens at the end: 503 + Retry-After (the default, for a client that cannot
    poll) or 202 with a pending session (for one that can).
    """
    _require_enabled()
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    if not catalogs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Workspace has no catalogs attached",
        )

    # Compute selection: explicit agent, else auto-pick a connected compatible one.
    # `agent` is one to dispatch to now; `requested_agent_id` is one that has to be
    # started first, in which case the session parks until it dials home.
    requested_agent_id: uuid.UUID | None = None
    restarting: Agent | None = None
    if body.agent_id is not None:
        agent = await db.get(Agent, body.agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        # Ahead of the connectivity probe, so a caller without access learns nothing
        # about the agent's state.
        await assert_agent_tier(db, user, agent, "use")
        if await is_agent_connected(db, agent.id):
            for catalog in catalogs:
                if not agent_supports_backend(agent.capabilities, catalog.storage_backend.kind):
                    ext = required_extension(catalog.storage_backend.kind)
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail={
                            "error": "agent_incompatible",
                            "detail": f"Agent '{agent.name}' is missing the '{ext}' extension.",
                        },
                    )
        elif _is_restartable_elastic(agent):
            # Start it and park, rather than failing: the reaper took this agent down
            # *because* nothing was using it, so refusing here would make an
            # idle-terminated agent permanently unusable (the reasoning the scheduler
            # already applies to an unattended run). Capabilities are deliberately not
            # checked -- a row that failed while provisioning carries none, so the
            # check would reject a perfectly restartable agent. The binder checks them
            # once the agent is up and has reported them.
            requested_agent_id, restarting, agent = agent.id, agent, None
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
            )
    else:
        agent = await pick_agent_for(db, workspace, principal_id=user.id)
        if agent is None and not settings.elastic_compute_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No connected agent available",
            )

    if body.catalog is not None:
        active = await resolve_catalog(db, workspace.id, body.catalog)
    else:
        active = await get_default_catalog(db, workspace.id)
    active = active or catalogs[0]

    # Which tool this is, from the User-Agent the client is expected to lead with its
    # calling application. Recorded here (once, at open) rather than per statement:
    # tagging per statement costs a round trip every time, and a tag that outlives a
    # failed materialization goes on reporting the wrong value afterwards.
    client_name, client_version = parse_user_agent(request.headers.get("user-agent"))

    now = datetime.now(tz=UTC)
    session = SqlSession(
        workspace_id=workspace.id,
        agent_id=agent.id if agent is not None else None,
        requested_agent_id=requested_agent_id,
        user_id=user.id,
        # No agent to open it yet: park until compute registers and the binder
        # claims it. Catalogs and the staging prefix are properties of the
        # workspace, so a pending session is fully described without one.
        status="opening" if agent is not None else "pending",
        opening_at=now if agent is not None else None,
        active_catalog=active.slug,
        client_name=client_name,
        client_version=client_version,
    )
    db.add(session)
    await db.flush()
    session.staging_uri = session_credentials.staging_uri_for(active, session.id)
    # Commit so the agent's SESSION_OPENED ack (applied in a separate DB session)
    # can find and update this row. For a pending session the same commit is what
    # lets the registration binder see it -- on Docker the agent can dial home
    # within a second of the provision call below.
    await db.commit()

    if agent is None:
        if not await _start_compute(db, session, workspace, restarting):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "compute_unavailable",
                    "detail": "Could not start compute for this session.",
                },
                headers={"Retry-After": "5"},
            )
    elif not await session_service.dispatch_open_session(db, session, catalogs):
        session.status = "failed"
        session.error = "agent not connected"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )

    if body.wait_timeout_s is not None:
        wait_s = body.wait_timeout_s
    elif agent is not None:
        # Warm path: an agent was already connected and has been told to open, so
        # the only thing left to wait for is its ack — the budget that has always
        # governed it. A deployment with warm compute sees no change at all.
        wait_s = settings.sql_session_open_timeout_s
    else:
        wait_s = settings.sql_session_wait_timeout_s
    if wait_s > 0:
        await session_service.await_session_open(db, session, wait_s)
    if session.status == "open":
        return session
    if body.on_wait_timeout == "continue" and session.status in ("pending", "opening"):
        # Hand the session back for the client to poll. Not a failure: compute is
        # still coming, and GET /sql/sessions/{id} follows it to `open`.
        response.status_code = status.HTTP_202_ACCEPTED
        return session
    if session.status == "pending":
        # Abandon the row with the same compare-and-set the open-timeout branch uses:
        # the binder may have claimed it between our last poll and here. The compute
        # that is starting is deliberately left alone -- an immediate client retry
        # should land on it, not pay for another cold start.
        result = await db.execute(
            sa.update(SqlSession)
            .where(SqlSession.id == session.id, SqlSession.status == "pending")
            .values(
                status="failed",
                error="compute_timeout",
                close_reason="compute_timeout",
                closed_at=datetime.now(tz=UTC),
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if result.rowcount == 1:
            record_sql_session_closed("compute_timeout")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "compute_starting",
                    "detail": "Compute is starting; retry shortly.",
                },
                headers={"Retry-After": "5"},
            )
        # The binder won the race: the row is opening or open now. Reload and fall
        # through to the same handling a warm open gets.
        await db.refresh(session)
        if session.status == "open":
            return session
    if session.status == "opening":
        # Time out the row with a compare-and-set so we never clobber a session the
        # agent opened between our last poll and here (it would flip opening→open in
        # a separate DB session). Capture the ids before the commit expires the row.
        session_id, agent_id = session.id, session.agent_id
        result = await db.execute(
            sa.update(SqlSession)
            .where(SqlSession.id == session_id, SqlSession.status == "opening")
            .values(
                status="failed",
                error="open_timeout",
                close_reason="open_timeout",
                closed_at=datetime.now(tz=UTC),
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if result.rowcount == 1:
            record_sql_session_closed("open_timeout")
            # The agent may still be finishing the open (and holding a slot); tell it
            # to drop. Best-effort — the agent's lease sweep is the durable backstop.
            with contextlib.suppress(Exception):
                await session_service.dispatch_close_session(db, agent_id, session_id)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Session open timed out"
            )
        # Agent won the race: the row is now open (or otherwise terminal). Reload.
        await db.refresh(session)
        if session.status == "open":
            return session
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "session_open_failed", "detail": session.error or "unknown"},
    )


@router.get("/workspaces/{ws}/sql/sessions", response_model=list[SqlSessionSummaryOut])
async def list_sessions(
    ws: str,
    status_filter: list[str] | None = QueryParam(default=None, alias="status"),
    user_id: uuid.UUID | None = QueryParam(default=None),
    agent_id: uuid.UUID | None = QueryParam(default=None),
    since: datetime | None = QueryParam(default=None),
    limit: int = QueryParam(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SqlSessionSummaryOut]:
    """The workspace's SQL sessions, newest first — the audit list.

    Any workspace member sees every session in the workspace, consistent with
    ``GET /workspaces/{ws}/queries``, which already returns every member's SQL.
    The ``user_id``/``agent_id``/``since`` filters are admin-only affordances and
    rejected with 403 for non-admins, mirroring that endpoint exactly.

    Each row carries the joined principal/agent names and its statement count so
    the list renders without a request per session.
    """
    _require_enabled()
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    # Statement counts in one grouped pass rather than N+1 per row.
    counts = (
        sa.select(Query.session_id, sa.func.count().label("statement_count"))
        .where(Query.session_id.isnot(None))
        .group_by(Query.session_id)
        .subquery()
    )
    stmt = (
        sa.select(SqlSession, User.name, Agent.name, sa.func.coalesce(counts.c.statement_count, 0))
        .outerjoin(User, SqlSession.user_id == User.id)
        .outerjoin(Agent, SqlSession.agent_id == Agent.id)
        .outerjoin(counts, counts.c.session_id == SqlSession.id)
        .where(SqlSession.workspace_id == workspace.id)
        .order_by(SqlSession.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(SqlSession.status.in_(status_filter))

    if any(f is not None for f in (user_id, agent_id, since)):
        if not await has_permission(db, user, Permission.QUERIES_ADMIN):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        if user_id:
            stmt = stmt.where(SqlSession.user_id == user_id)
        if agent_id:
            stmt = stmt.where(SqlSession.agent_id == agent_id)
        if since:
            stmt = stmt.where(SqlSession.created_at >= since)

    return [
        SqlSessionSummaryOut.model_validate(session).model_copy(
            update={
                "user_name": user_name,
                "agent_name": agent_name,
                "statement_count": statement_count,
            }
        )
        for session, user_name, agent_name, statement_count in (await db.execute(stmt)).all()
    ]


@router.get("/sql/sessions/{session_id}/statements", response_model=list[QueryOut])
async def list_session_statements(
    session_id: uuid.UUID,
    limit: int = QueryParam(default=200, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Query]:
    """A session's statements in execution order — the statement timeline.

    Ascending, unlike the newest-first history feeds: a session is one workload
    read top to bottom, so a `dbt run` reads as the sequence it actually was.
    """
    _require_enabled()
    session = await _load_session(db, session_id, user)
    return list(
        (
            await db.execute(
                sa.select(Query)
                .where(Query.session_id == session.id)
                .order_by(Query.started_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


@router.get("/sql/sessions/{session_id}", response_model=SqlSessionOut)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SqlSession:
    _require_enabled()
    return await _load_session(db, session_id, user)


@router.post("/sql/sessions/{session_id}/statements", status_code=202, response_model=QueryOut)
async def run_statement(
    session_id: uuid.UUID,
    body: SqlStatementCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
    _require_enabled()
    session = await _load_session(db, session_id, user)
    if session.status != "open":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "session_not_open", "detail": f"session is {session.status}"},
        )

    catalogs = await resolve_workspace_catalogs(db, session.workspace_id)
    managed = {c.slug for c in catalogs} | {c.polaris_name for c in catalogs}
    # Admit both the object-storage staging prefix (COPY / s3:// reads) and its
    # HTTPS form so a staged `read_parquet('<presigned get_url>')` is allowed,
    # while arbitrary local-FS / external URLs still reject.
    prefixes = session_credentials.staging_prefixes(session.staging_uri)
    active = next((c for c in catalogs if c.slug == session.active_catalog), None)
    if active is not None:
        prefixes = prefixes + staging_presign.staging_read_prefixes(active, session.id)

    # Capability-scoped statement policy (the session path's relaxed I8).
    try:
        policy.assert_statement_allowed(
            body.sql, staging_prefixes=prefixes, managed_catalogs=managed
        )
    except policy.StatementNotAllowed as exc:
        record_statement_policy_rejection(exc.rule)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "statement_not_allowed", "detail": str(exc)},
        ) from exc

    # Migration write-freeze (same gate as the query path).
    if not is_read_only(body.sql) and await workspace_has_active_migration(
        db, session.workspace_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "catalog_read_only", "detail": "a storage migration is in progress"},
        )

    # Per-statement authorization (scoped catalogs); no-op for open workspaces.
    try:
        await assert_query_access(
            db, session.workspace_id, session.user_id, body.sql, session.active_catalog, catalogs
        )
    except GrantDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "grant_denied", "detail": str(exc)},
        ) from exc

    query = Query(
        workspace_id=session.workspace_id,
        agent_id=session.agent_id,
        user_id=session.user_id,
        sql=body.sql,
        status="queued",
        origin="session",
        session_id=session.id,
        # Persisted so the reaper can bound this statement server-side; the agent
        # enforces the same budget around execution.
        timeout_s=body.timeout_s,
    )
    db.add(query)
    session.last_active_at = datetime.now(tz=UTC)
    # Commit before dispatching: STATEMENT_ACK is applied by a separate DB
    # connection (the websocket receive loop), and can round-trip back from the
    # agent faster than this transaction would otherwise commit. A merely
    # flush()ed row is invisible to that connection under read-committed
    # isolation, so the ack would silently no-op and the row would never leave
    # `queued` until QUERY_DONE arrives.
    await db.commit()

    if not await session_service.dispatch_exec_statement(db, session, query, body.timeout_s):
        query.status = "failed"
        query.error = "agent not connected"
        query.finished_at = datetime.now(tz=UTC)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )
    return query


@router.post("/sql/sessions/{session_id}/staging-files", response_model=StagingFilesOut)
async def create_staging_files(
    session_id: uuid.UUID,
    body: StagingFilesCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StagingFilesOut:
    """Presign a PUT (upload) and GET (read) URL per file under the session's
    stage. Backend-specific presigning stays in the API; the client uploads with a
    plain HTTP PUT and the agent reads the GET URL over httpfs (no storage secret).
    """
    _require_enabled()
    session = await _load_session(db, session_id, user)
    # A reaped/closed session (or one whose agent dropped — the reaper reflects it
    # in status) can no longer stage; mirror the statement path's lost-session 409.
    if session.status != "open":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "session_not_open", "detail": f"session is {session.status}"},
        )

    catalog = await resolve_catalog(db, session.workspace_id, session.active_catalog)
    try:
        files, expires_at = await asyncio.to_thread(
            staging_presign.presign_staging_files,
            catalog,
            session.id,
            body.files,
            ttl_s=settings.sql_session_staging_url_ttl_s,
        )
    except staging_presign.StagingUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "staging_unavailable", "detail": str(exc)},
        ) from exc

    return StagingFilesOut(
        files=[
            StagedFileOut(name=f.name, key=f.key, put_url=f.put_url, get_url=f.get_url)
            for f in files
        ],
        expires_at=expires_at,
    )


@router.delete("/sql/sessions/{session_id}", status_code=204)
async def close_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    _require_enabled()
    session = await _load_session(db, session_id, user)
    if session.status in ("closed", "expired", "failed"):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    session.status = "closing"
    await db.commit()
    if session.agent_id is not None:
        await session_service.dispatch_close_session(db, session.agent_id, session.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
