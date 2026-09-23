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
import contextlib
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from api.metrics import record_sql_session_closed, record_sql_session_opened, record_sql_statement
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.services.agent_dispatch import send_to_agent
from api.services.session_credentials import build_catalog_attach, build_polaris_block
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.telemetry import inject_trace_context

_tracer = trace.get_tracer("duckhaven.api")

_TERMINAL = ("closed", "expired", "failed")
# States the open call is still waiting on: `pending` (compute starting, no agent
# bound yet) and `opening` (bound, awaiting the agent's ack).
_WAITING = ("pending", "opening")
# A statement's non-terminal states, the ones a completion wait sits through.
# Mirrors the SQL connector's own `_PENDING`, which drives its poll loop.
_STATEMENT_PENDING = ("queued", "running")


async def _catalog_descriptors(catalogs: list[Catalog]) -> list[dict[str, object]]:
    return [await build_catalog_attach(c) for c in catalogs]


async def dispatch_open_session(
    db: AsyncSession, session: SqlSession, catalogs: list[Catalog]
) -> bool:
    """Instruct the pinned agent to open + attach a held connection for a session.

    Carries the API-vended Polaris block (the credential seam) so the agent builds
    its iceberg SECRET from API-supplied credentials, not its own config."""
    payload: dict[str, object] = {
        "session_id": str(session.id),
        "active_catalog": session.active_catalog,
        "catalogs": await _catalog_descriptors(catalogs),
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


_IN_FLIGHT = ("queued", "running")


async def fail_inflight_statements(
    db: AsyncSession, session_ids: list[uuid.UUID], error: str
) -> int:
    """Resolve the statements still in flight on sessions that just went terminal.

    A session's statements can only run on its agent's held connection, so once the
    session is gone they never will. Without this they stay ``queued`` forever and
    every client polls one until its own deadline (#156). Returns the row count;
    the caller commits.
    """
    if not session_ids:
        return 0
    result = await db.execute(
        sa.update(Query)
        .where(
            Query.session_id.in_(session_ids),
            Query.origin == "session",
            Query.status.in_(_IN_FLIGHT),
        )
        .values(status="failed", error=error, finished_at=datetime.now(tz=UTC))
    )
    count = result.rowcount or 0
    for _ in range(max(0, count)):
        record_sql_statement("failed")
    return count


async def handle_statement_ack(db: AsyncSession, frame: Frame) -> None:
    """Apply an agent's STATEMENT_ACK receipt: the statement reached the agent.

    Only ``queued`` -> ``running``. A fast statement's QUERY_DONE can beat its own
    ack (both are sent by the agent but applied by separate DB sessions), so an
    already-terminal row is left alone rather than resurrected.
    """
    query = await db.get(Query, uuid.UUID(frame.payload["query_id"]))
    if query is None or query.status != "queued":
        return
    query.status = "running"
    await db.commit()


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
        elif frame.payload.get("status") == "open":
            # A successful open for a row we already gave up on (reaped at the
            # opening deadline, or failed with the agent). The agent is holding a
            # connection and an admission slot for a session nobody will ever use,
            # so tell it to drop them — without this it keeps both until it restarts.
            agent_id, session_id = session.agent_id, session.id
            await db.commit()
            if agent_id is not None:
                with contextlib.suppress(Exception):
                    await dispatch_close_session(db, agent_id, session_id)
            return
        elif session.status not in _TERMINAL:
            session.status = "failed"
            session.error = frame.payload.get("error")
            session.close_reason = "failed"
            session.closed_at = now
            record_sql_session_closed("failed")
    elif frame.type == FrameType.SESSION_CLOSED:
        if session.status == "closing":
            session.status = "closed"
            session.close_reason = "client"
            session.closed_at = now
            record_sql_session_closed("client")
        elif session.status not in _TERMINAL:
            session.status = "closed"
            session.closed_at = now
            # The agent self-reaped an orphaned held session (lease expiry); count it
            # under the reason it reports so the backstop is observable.
            reason = frame.payload.get("reason")
            if reason:
                session.close_reason = reason
                record_sql_session_closed(reason)
        # The held connection is gone either way, so nothing still queued on this
        # session can ever run.
        await fail_inflight_statements(db, [session.id], "session closed")
    await db.commit()


async def fail_sessions_for_agent(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Fail every non-terminal session pinned to an agent that just disconnected.

    Postgres is the state-of-record: the agent's held connections are gone (or will
    be dropped on its reconnect), so the sessions cannot continue. The next
    statement on such a session returns 409; the client reopens. Their in-flight
    statements are resolved too — they were running on the connections that just
    died.

    Deliberately matches on ``agent_id`` only, so a ``pending`` session (no agent
    yet) survives: it is waiting on *compute*, not on this socket, and another
    agent coming up for the pool can still open it. The provisioning deadline
    bounds it instead (``compute.reaper._fail_stranded_pending``)."""
    now = datetime.now(tz=UTC)
    result = await db.execute(
        sa.update(SqlSession)
        .where(
            SqlSession.agent_id == agent_id,
            SqlSession.status.in_(["opening", "open", "closing"]),
        )
        .values(
            status="failed",
            error="agent disconnected",
            close_reason="agent_disconnect",
            closed_at=now,
        )
        .returning(SqlSession.id)
    )
    session_ids = list(result.scalars().all())
    await fail_inflight_statements(db, session_ids, "agent disconnected")
    await db.commit()
    for _ in range(len(session_ids)):
        record_sql_session_closed("agent_disconnect")


async def await_session_open(
    db: AsyncSession,
    session: SqlSession,
    timeout_s: float,
    poll_interval_s: float = 0.1,
    poll_max_s: float = 1.0,
) -> SqlSession:
    """Block until the session stops waiting: on an agent to start (``pending``) or
    on that agent's SESSION_OPENED ack (``opening``).

    Both transitions are applied by another DB session — the registration binder
    and the WebSocket handler respectively — so we poll this session's view
    (mirrors ``run_sync_query``). On timeout the row is left where it was and the
    caller decides what to do with it.

    The transaction is rolled back before each sleep so the pooled connection is
    returned between polls. Holding it would pin one of ``db_pool_size +
    db_max_overflow`` connections idle-in-transaction for the whole wait, and a
    cold start is exactly when many clients wait at once (a `dbt run` opens one
    session per thread). The interval backs off for the same reason: a long wait
    should not cost hundreds of round trips.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    interval = poll_interval_s
    while asyncio.get_event_loop().time() < deadline:
        await db.rollback()
        await asyncio.sleep(interval)
        interval = min(poll_max_s, interval * 1.5)
        await db.refresh(session)
        if session.status not in _WAITING:
            break
    return session


async def await_query_done(
    db: AsyncSession,
    query: Query,
    timeout_s: float,
    poll_interval_s: float = 0.05,
    poll_max_s: float = 0.5,
) -> Query:
    """Block until the statement reaches a terminal state, or ``timeout_s`` runs out.

    The sibling of :func:`await_session_open`, for the other thing a client would
    otherwise sit and poll for. On timeout the row is left alone and the caller
    hands it back for the client to poll — a statement is never cancelled for
    outliving a wait.

    **Woken by the completion event, not by the poll.** QUERY_DONE arrives on the
    agent's WebSocket, so this process already learns of completion the moment it
    happens; ``query.completion_waiter`` turns that into an event and each sleep
    below ends early on it. Polling alone would have been a mistake worth naming:
    the first cut of this did exactly that, and a benchmark showed statement wall
    times quantised onto *this* function's backoff grid — the same staircase the
    wait exists to remove, moved from the client to the server and, for a statement
    landing just past a step, worse than before.

    The poll remains as the backstop for the case the event cannot cover: the
    agent's socket owned by another replica, or a statement failed by the reaper
    rather than by the agent. It backs off because in those cases it may run for
    the whole budget.

    ``await_session_open``'s connection discipline applies and matters more here,
    since this waits on every statement rather than only a cold start: the
    transaction is rolled back before each sleep so the pooled connection goes back
    for the duration. Holding it would cap concurrent statements at ``db_pool_size
    + db_max_overflow`` and deadlock a `dbt run` with more threads than that.
    """
    from api.services.query import completion_waiter

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    interval = poll_interval_s
    with completion_waiter(query.id) as done:
        while True:
            await db.refresh(query)
            if query.status not in _STATEMENT_PENDING:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            # After the read, not before it: `refresh` opens a transaction, and
            # leaving it open across the wait is what pins the pooled connection.
            await db.rollback()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=min(interval, remaining))
            interval = min(poll_max_s, interval * 1.5)
    return query
