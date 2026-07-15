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

import contextlib
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_current_user, get_db
from api.metrics import record_sql_session_closed, record_statement_policy_rejection
from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.user import User
from api.schemas.query import QueryOut
from api.schemas.sql_session import SqlSessionCreate, SqlSessionOut, SqlStatementCreate
from api.services import session_credentials
from api.services import statement_policy as policy
from api.services.agent_capabilities import agent_supports_backend, required_extension
from api.services.agent_dispatch import is_agent_connected
from api.services.grants import GrantDenied, assert_query_access
from api.services.migration.service import workspace_has_active_migration
from api.services.query import pick_agent_for
from api.services.sql_guard import is_read_only
from api.services.sql_sessions import service as session_service
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


@router.post("/workspaces/{ws}/sql/sessions", status_code=201, response_model=SqlSessionOut)
async def open_session(
    ws: str,
    body: SqlSessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SqlSession:
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
    if body.agent_id is not None:
        agent = await db.get(Agent, body.agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        if not await is_agent_connected(db, agent.id):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
            )
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
    else:
        agent = await pick_agent_for(db, workspace)
        if agent is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No connected agent available",
            )

    if body.catalog is not None:
        active = await resolve_catalog(db, workspace.id, body.catalog)
    else:
        active = await get_default_catalog(db, workspace.id)
    active = active or catalogs[0]

    session = SqlSession(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=user.id,
        status="opening",
        active_catalog=active.slug,
    )
    db.add(session)
    await db.flush()
    session.staging_uri = session_credentials.staging_uri_for(active, session.id)
    # Commit so the agent's SESSION_OPENED ack (applied in a separate DB session)
    # can find and update this row.
    await db.commit()

    if not await session_service.dispatch_open_session(db, session, catalogs):
        session.status = "failed"
        session.error = "agent not connected"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )

    await session_service.await_session_open(db, session, settings.sql_session_open_timeout_s)
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
            .values(status="failed", error="open_timeout", closed_at=datetime.now(tz=UTC))
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
    prefixes = session_credentials.staging_prefixes(session.staging_uri)

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
    )
    db.add(query)
    await db.flush()

    if not await session_service.dispatch_exec_statement(db, session, query, body.timeout_s):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )
    session.last_active_at = datetime.now(tz=UTC)
    await db.commit()
    return query


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
