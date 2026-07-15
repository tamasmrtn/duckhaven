"""SQL session brokering: dispatch session frames to the pinned agent, apply the
agent's lifecycle acks, and reconcile sessions when an agent disconnects.

Every frame rides the agent-initiated WebSocket (I2 preserved) via
``send_to_agent`` (which forwards cross-replica by the Postgres ``owner_url``), so
a session survives API failover as long as its pinned agent stays connected. The
``sql_sessions`` rows are the state-of-record (I9); the agent's held connection is
ephemeral socket state.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from api.metrics import record_sql_session_closed, record_sql_session_opened
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.services.agent_dispatch import send_to_agent
from api.services.session_credentials import build_polaris_block
from api.services.workspace import DEFAULT_SCHEMA
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.telemetry import inject_trace_context

_tracer = trace.get_tracer("duckhaven.api")

_TERMINAL = ("closed", "expired", "failed")


def _catalog_descriptors(catalogs: list[Catalog]) -> list[dict[str, object]]:
    return [
        {
            "slug": c.slug,
            "polaris_name": c.polaris_name,
            "backend": {"kind": c.storage_backend.kind, "root_uri": c.storage_backend.root_uri},
            "default_schema": DEFAULT_SCHEMA,
        }
        for c in catalogs
    ]


async def dispatch_open_session(
    db: AsyncSession, session: SqlSession, catalogs: list[Catalog]
) -> bool:
    """Instruct the pinned agent to open + attach a held connection for a session.

    Carries the API-vended Polaris block (the credential seam) so the agent builds
    its iceberg SECRET from API-supplied credentials, not its own config."""
    payload: dict[str, object] = {
        "session_id": str(session.id),
        "active_catalog": session.active_catalog,
        "catalogs": _catalog_descriptors(catalogs),
        "polaris": build_polaris_block(),
    }
    with _tracer.start_as_current_span(
        "open_session",
        kind=trace.SpanKind.PRODUCER,
        attributes={
            "duckhaven.session_id": str(session.id),
            "duckhaven.agent_id": str(session.agent_id),
        },
    ):
        frame = Frame(
            type=FrameType.OPEN_SESSION, payload=payload, trace_context=inject_trace_context()
        )
        return await send_to_agent(db, session.agent_id, frame.model_dump_json())


async def dispatch_exec_statement(
    db: AsyncSession, session: SqlSession, query: Query, timeout_s: float
) -> bool:
    """Run one statement on the session's held connection. Completion comes back as
    an ordinary QUERY_DONE keyed by ``query.id`` (handled by query.handle_agent_frame)."""
    payload: dict[str, object] = {
        "session_id": str(session.id),
        "query_id": str(query.id),
        "sql": query.sql,
        "timeout_s": timeout_s,
    }
    with _tracer.start_as_current_span(
        "exec_statement",
        kind=trace.SpanKind.PRODUCER,
        attributes={
            "duckhaven.session_id": str(session.id),
            "duckhaven.statement_id": str(query.id),
        },
    ):
        frame = Frame(
            type=FrameType.EXEC_STATEMENT, payload=payload, trace_context=inject_trace_context()
        )
        return await send_to_agent(db, session.agent_id, frame.model_dump_json())


async def dispatch_close_session(
    db: AsyncSession, agent_id: uuid.UUID, session_id: uuid.UUID
) -> bool:
    with _tracer.start_as_current_span(
        "close_session",
        kind=trace.SpanKind.PRODUCER,
        attributes={"duckhaven.session_id": str(session_id)},
    ):
        frame = Frame(
            type=FrameType.CLOSE_SESSION,
            payload={"session_id": str(session_id)},
            trace_context=inject_trace_context(),
        )
        return await send_to_agent(db, agent_id, frame.model_dump_json())


async def handle_session_frame(db: AsyncSession, frame: Frame) -> None:
    """Apply an agent's SESSION_OPENED / SESSION_CLOSED lifecycle ack to the row."""
    session = await db.get(SqlSession, uuid.UUID(frame.payload["session_id"]))
    if session is None:
        return
    now = datetime.now(tz=UTC)

    if frame.type == FrameType.SESSION_OPENED:
        if frame.payload.get("status") == "open" and session.status == "opening":
            session.status = "open"
            session.opened_at = now
            session.last_active_at = now
            record_sql_session_opened()
        elif session.status not in _TERMINAL:
            session.status = "failed"
            session.error = frame.payload.get("error")
            session.closed_at = now
            record_sql_session_closed("failed")
    elif frame.type == FrameType.SESSION_CLOSED:
        if session.status == "closing":
            session.status = "closed"
            session.closed_at = now
            record_sql_session_closed("client")
        elif session.status not in _TERMINAL:
            session.status = "closed"
            session.closed_at = now
            # The agent self-reaped an orphaned held session (lease expiry); count it
            # under the reason it reports so the backstop is observable.
            reason = frame.payload.get("reason")
            if reason:
                record_sql_session_closed(reason)
    await db.commit()


async def fail_sessions_for_agent(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Fail every non-terminal session pinned to an agent that just disconnected.

    Postgres is the state-of-record: the agent's held connections are gone (or will
    be dropped on its reconnect), so the sessions cannot continue. The next
    statement on such a session returns 409; the client reopens."""
    now = datetime.now(tz=UTC)
    result = await db.execute(
        sa.update(SqlSession)
        .where(
            SqlSession.agent_id == agent_id,
            SqlSession.status.in_(["opening", "open", "closing"]),
        )
        .values(status="failed", error="agent disconnected", closed_at=now)
    )
    await db.commit()
    for _ in range(max(0, result.rowcount or 0)):
        record_sql_session_closed("agent_disconnect")


async def await_session_open(
    db: AsyncSession, session: SqlSession, timeout_s: float, poll_interval_s: float = 0.1
) -> SqlSession:
    """Block until the agent's SESSION_OPENED ack flips the row out of ``opening``.

    The ack is applied by the WebSocket handler in a separate session, so we poll
    this session's view (mirrors ``run_sync_query``). On timeout the row is left
    ``opening`` and the caller surfaces it as still-opening / failed."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval_s)
        await db.refresh(session)
        if session.status != "opening":
            break
    return session
