import asyncio
import base64
import binascii
import contextlib
import datetime as dt
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import duckdb
import httpx
import sqlalchemy as sa
from fastapi import HTTPException, status
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.metrics import (
    record_query_completion,
    record_query_queue_rejection,
    record_query_queue_wait,
    record_query_submitted,
    record_rows_decode,
    record_rows_proxy,
    record_sql_statement,
)
from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.table_metadata import TableMetadata
from api.models.user import Credential
from api.models.workspace import Workspace
from api.schemas.query import RowsPageOut
from api.services import agent_access
from api.services import grants as grant_service
from api.services import runtimes as runtime_service
from api.services.agent_capabilities import agent_supports_catalog
from api.services.agent_dispatch import (
    connected_agent_ids,
    is_agent_connected,
    send_to_agent,
)
from api.services.catalog_refs import dispatch_catalogs, statement_catalogs
from api.services.migration.service import workspace_has_active_migration
from api.services.sql_guard import is_read_only
from api.services.workspace import (
    get_default_catalog,
    resolve_workspace_catalogs,
)
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.telemetry import inject_trace_context

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("duckhaven.api")


class Completion(asyncio.Event):
    """Set when a statement reaches a terminal state here. ``row`` is the terminal
    row as committed, by attribute name, when the setter had it: the waiter takes
    it rather than reading back what this process has just written.
    ``result_parquet`` is the statement's whole result file when the agent sent it
    along with QUERY_DONE, so the waiter can answer without fetching it back."""

    row: dict[str, Any] | None = None
    result_parquet: bytes | None = None


# Requests waiting for a statement to finish, by query id (see
# sql_sessions.service.await_query_done). QUERY_DONE arrives on the agent's
# WebSocket, so completion is an *event* this process already observes -- a waiter
# that polled for it instead would rediscover the very staircase the wait exists to
# remove, just at a finer granularity.
#
# In-process only, and deliberately: it is an optimisation over the waiter's own
# backstop poll, not a mechanism the correctness depends on. When the agent's socket
# is owned by another replica nothing here fires and the waiter falls back to
# polling, which is what it would have done anyway.
_completion_waiters: dict[uuid.UUID, Completion] = {}


@contextlib.contextmanager
def completion_waiter(query_id: uuid.UUID):
    """Register an event fired when ``query_id`` reaches a terminal state here."""
    event = Completion()
    _completion_waiters[query_id] = event
    try:
        yield event
    finally:
        _completion_waiters.pop(query_id, None)


def signal_completion(
    query_id: uuid.UUID,
    row: dict[str, Any] | None = None,
    result_parquet: bytes | None = None,
) -> None:
    """Wake anything waiting on ``query_id``. Called after the status is committed,
    so a woken waiter's next read is guaranteed to see the terminal row, or need
    not read it at all when ``row`` is that row."""
    event = _completion_waiters.get(query_id)
    if event is not None:
        event.row = row
        event.result_parquet = result_parquet
        event.set()


# Larger than any agent sends by default (its `SESSION_RESULT_INLINE_MAX_BYTES` is
# 256 KiB): a bound on what one frame may make this process hold, not a tuning knob.
_MAX_INLINE_RESULT_BYTES = 1024 * 1024


def _inline_result(encoded: Any) -> bytes | None:
    """The result file an agent put in its QUERY_DONE, or None when there is none
    or it cannot be used. The waiter then fetches the file as it always did."""
    if not isinstance(encoded, str) or len(encoded) > _MAX_INLINE_RESULT_BYTES * 4 // 3 + 4:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except binascii.Error:
        return None


class AgentUnavailable(ValueError):
    """The agent a query targets has no usable socket.

    Distinguished from the other dispatch failures so a caller can react to it
    specifically: for a run against the elastic pool this is not an error at all, it
    just means supply has to be provisioned. A ``ValueError`` subclass so callers with
    an existing blanket ``except ValueError`` — the scheduler and maintenance scanner —
    keep behaving as they did.
    """


async def dispatch_query(
    db: AsyncSession,
    query: Query,
    *,
    timeout_s: float = 600.0,
    active_catalog: str | None = None,
    principal_id: uuid.UUID | None = None,
    stats_for: dict[str, str] | None = None,
    health_for: dict[str, object] | None = None,
    maintain_for: dict[str, object] | None = None,
) -> None:
    if query.agent_id is None or not await is_agent_connected(db, query.agent_id):
        raise AgentUnavailable("Agent not connected")

    workspace = await db.get(Workspace, query.workspace_id)
    if workspace is None:
        raise ValueError("Workspace missing for query")
    # Read-only freeze: while any attached catalog is mid storage-migration, reject
    # writes (reads still flow). Conservative — blocks writes in the whole workspace.
    if not is_read_only(query.sql) and await workspace_has_active_migration(db, workspace.id):
        raise ValueError("Catalog is read-only: a storage backend migration is in progress")
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    if not catalogs:
        raise ValueError("Workspace has no catalogs attached")

    # The one check every dispatch path passes through — interactive runs, the
    # scheduler, maintenance, and work parked while an agent was starting — so an
    # agent missing an extension, or on a runtime that isn't trusted with work,
    # never receives it however the query reached it.
    agent = await db.get(Agent, query.agent_id)
    runtime_service.assert_dispatchable(agent, catalogs)
    query.runtime_id = runtime_service.runtime_id_of(agent)

    # The agent ATTACHes catalogs under their slugs and `USE`s the active one.
    if active_catalog is None:
        default = await get_default_catalog(db, workspace.id)
        active_catalog = default.slug if default is not None else catalogs[0].slug
    # Recorded so reading the result later re-checks grants where names resolved,
    # not wherever the workspace default happens to point by then.
    query.active_catalog = active_catalog

    # Scoped-catalog grant check: reject before dispatch if the principal lacks
    # tier on any referenced object. No-op unless a catalog is in scoped mode.
    await grant_service.assert_query_access(
        db,
        workspace.id,
        principal_id if principal_id is not None else query.user_id,
        query.sql,
        active_catalog,
        catalogs,
    )
    payload: dict[str, object] = {
        "query_id": str(query.id),
        "sql": query.sql,
        "timeout_s": timeout_s,
        "active_catalog": active_catalog,
    }
    # Only the catalogs the statement can name, for an agent that fetches the
    # rest on demand. Internal probes name their table's catalog explicitly.
    probed = [t["catalog"] for t in (stats_for, health_for, maintain_for) if t and t.get("catalog")]
    attach = statement_catalogs(query.sql, catalogs, active_catalog, also=probed)
    payload.update(await dispatch_catalogs(agent, catalogs, attach))
    if stats_for is not None:
        # Ask the agent to also compute true table stats for this table.
        payload["stats_for"] = stats_for
    if health_for is not None:
        # Ask the agent to run the maintenance health probe for this table.
        payload["health_for"] = health_for
    if maintain_for is not None:
        payload["maintain_for"] = maintain_for

    # Producer span for the WebSocket hop; its context rides in the frame so
    # the agent's consumer span joins this trace.
    with _tracer.start_as_current_span(
        "dispatch_query",
        kind=trace.SpanKind.PRODUCER,
        attributes={
            "duckhaven.query_id": str(query.id),
            "duckhaven.agent_id": str(query.agent_id),
            "duckhaven.runtime": query.runtime_id or "",
            # null origin = a user's interactive query; else "scheduled"/etc.
            "duckhaven.origin": query.origin or "interactive",
        },
    ):
        frame = Frame(
            type=FrameType.DISPATCH_QUERY,
            payload=payload,
            trace_context=inject_trace_context(),
        )
        if not await send_to_agent(db, query.agent_id, frame.model_dump_json()):
            # The socket vanished between the presence check and the send, or its
            # owning replica is unreachable. Fail fast so the caller surfaces it.
            raise AgentUnavailable("Agent not connected")
    # Mark the (possibly elastic) agent as having done work now, so the idle
    # reaper doesn't scale it in under an active workload. No-op for static agents.
    from api.services.compute.service import record_activity

    await record_activity(db, query.agent_id)
    # Status stays "queued" until the agent admits the query and emits
    # QUERY_PROGRESS; the agent may hold it in its admission queue first.
    if query.origin is None:
        record_query_submitted()
    await db.commit()


def _utc(value: datetime) -> datetime:
    """A timestamp read back from the database, as an aware UTC datetime (SQLite
    returns them naive)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def handle_agent_frame(db: AsyncSession, frame: Frame, polaris=None) -> None:
    query_id = uuid.UUID(frame.payload["query_id"])
    if frame.type == FrameType.QUERY_PROGRESS:
        progress = {k: v for k, v in frame.payload.items() if k != "query_id"}
        now = datetime.now(tz=UTC)
        # One statement, not a read then a write. The status condition is what
        # keeps a late frame (the reaper already failed the row) from bringing a
        # terminal query back to life. The first progress frame is when execution
        # started, so it stamps running_at, kept by every later one. A session
        # statement is already "running" once its receipt is acked, before it gets
        # the session lock, so it is the missing running_at, not the status, that
        # marks it.
        row = (
            await db.execute(
                sa.update(Query)
                .where(Query.id == query_id, Query.status.in_(("queued", "running")))
                .values(
                    status="running",
                    progress=progress or None,
                    running_at=sa.func.coalesce(Query.running_at, now),
                )
                .returning(Query.origin, Query.started_at, Query.running_at)
                .execution_options(synchronize_session=False)
            )
        ).first()
        await db.commit()
        # This frame set running_at only if it was the first, so this is the
        # queued -> running transition: record how long an interactive query
        # waited in the agent's admission queue before it started executing.
        if row is not None and row.origin is None and _utc(row.running_at) == now:
            record_query_queue_wait((now - _utc(row.started_at)).total_seconds())
        return
    if frame.type == FrameType.QUERY_DONE:
        # Never stored: only a request in this process waiting on the statement
        # uses it, to skip fetching the file back from the agent.
        encoded_result = frame.payload.pop("result_parquet", None)
        status_val = frame.payload.get("status", "done")
        finished = datetime.now(tz=UTC)
        statement = await db.get(Query, query_id)
        if statement is not None and statement.origin == "session":
            from api.services.result_cache.session import record_completion

            # In the commit below, with the status: a client that sends its next
            # statement as soon as it hears this one finished must already see the
            # session state this one left behind.
            await record_completion(db, statement, frame.payload)
        # A query fast enough to finish without ever emitting QUERY_PROGRESS has no
        # running_at yet. Back it out of the agent's own execution time so the
        # queued/running split stays honest instead of reporting the whole
        # wall-clock as queue wait. COALESCE leaves an already-stamped value alone.
        duration_ms = frame.payload.get("duration_ms")
        ran_at = finished - timedelta(milliseconds=duration_ms) if duration_ms else finished
        update = (
            sa.update(Query)
            .where(Query.id == query_id)
            .values(
                status=status_val,
                row_count=frame.payload.get("row_count"),
                duration_ms=duration_ms,
                result_bytes=frame.payload.get("result_bytes"),
                error=frame.payload.get("error"),
                result_path=frame.payload.get("result_path"),
                result_schema=frame.payload.get("result_schema"),
                profile=frame.payload.get("profile"),
                running_at=sa.func.coalesce(Query.running_at, ran_at),
                finished_at=finished,
            )
        )
        # The whole row, but only for a request here waiting on this statement:
        # the SQL and profile can be large, and most completions have no waiter.
        columns = sa.inspect(Query).columns if query_id in _completion_waiters else None
        if columns is not None:
            update = update.returning(*columns)
        result = await db.execute(update)
        terminal = result.first() if columns is not None else None
        await db.commit()
        # Before the stats/lineage work below, which is deliberately not on the
        # client's critical path: the row is terminal and committed as of here.
        signal_completion(
            query_id,
            None if terminal is None else {k: terminal._mapping[c] for k, c in columns.items()},
            _inline_result(encoded_result)
            if columns is not None and status_val == "done"
            else None,
        )
        query = await db.get(Query, query_id)
        if query is not None and query.origin is None:
            record_query_completion(
                status_val,
                frame.payload.get("duration_ms"),
                frame.payload.get("result_bytes"),
                frame.payload.get("error"),
            )
            if status_val == "failed":
                record_query_queue_rejection(frame.payload.get("error"))
        elif query is not None and query.origin == "session":
            # Session statements are counted on their own series (kept out of the
            # interactive-query counters above).
            record_sql_statement(status_val)
        if query is not None and query.origin == "maintenance_apply":
            from api.services.maintenance.apply import record_apply_result

            await record_apply_result(db, query, frame.payload)
        if status_val == "done" and query is not None and query.cache_status == "miss":
            from api.services.result_cache.service import schedule_admission

            schedule_admission(query.id, polaris)
        if status_val == "done":
            await _upsert_table_stats(db, query_id, frame)
            health = frame.payload.get("health")
            if health and query is not None:
                from api.services.maintenance.ingest import record_health_sample

                await record_health_sample(db, query, health)
            if query is not None:
                await _record_lineage(db, query, polaris)


async def _record_lineage(db: AsyncSession, query: Query, polaris=None) -> None:
    """Derive lineage for a completed query, never at the query's expense.

    Runs on the agent's frame-receive path, so every failure mode is swallowed:
    a graph that misses an edge is a much smaller problem than a frame handler
    that stops processing an agent's traffic. Should this ever show up in
    profiling, it can move to a background loop without any model change.

    ``polaris`` is what column-level extraction reads source table schemas
    through, and it is only consulted for the two statement shapes that cannot be
    resolved without one — ``SELECT *``, and a multi-source query naming a column
    without saying which relation it belongs to. Everything else costs no round
    trip. Without a client the graph stays table-level rather than failing.
    """
    from api.services.lineage.columns import CatalogSchemaLookup
    from api.services.lineage.ingest import record_execution_lineage, workspace_catalog_context

    # Only a write establishes a relationship, so a statement made only of reads
    # has none to record: skip it before reading the workspace's catalogs and
    # committing, which every completed query otherwise paid for nothing.
    if not query.sql or is_read_only(query.sql):
        return
    try:
        context = await workspace_catalog_context(db, query.workspace_id)
        schemas = (
            CatalogSchemaLookup(polaris, {c.id: c for c in context.catalogs})
            if polaris is not None
            else None
        )
        await record_execution_lineage(db, query, schemas=schemas, context=context)
        await db.commit()
    except Exception:
        logger.exception("Lineage extraction failed for query %s", query.id)
        await db.rollback()


async def _upsert_table_stats(db: AsyncSession, query_id: uuid.UUID, frame: Frame) -> None:
    """Persist agent-computed table row/size stats reported alongside a query."""
    stats = frame.payload.get("stats_table")
    if not stats:
        return
    schema_name = stats.get("schema")
    table_name = stats.get("table")
    catalog_slug = stats.get("catalog")
    if not schema_name or not table_name or not catalog_slug:
        return
    query = await db.get(Query, query_id)
    if query is None:
        return
    catalog = (
        await db.execute(sa.select(Catalog).where(Catalog.slug == catalog_slug))
    ).scalar_one_or_none()
    if catalog is None:
        return
    existing = (
        await db.execute(
            sa.select(TableMetadata).where(
                TableMetadata.catalog_id == catalog.id,
                TableMetadata.schema_name == schema_name,
                TableMetadata.table_name == table_name,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = TableMetadata(
            catalog_id=catalog.id,
            schema_name=schema_name,
            table_name=table_name,
        )
        db.add(existing)
    row_count = frame.payload.get("table_row_count")
    size_bytes = frame.payload.get("table_size_bytes")
    if row_count is not None:
        existing.row_count = row_count
    if size_bytes is not None:
        existing.size_bytes = size_bytes

    # Format-native metadata from the agent probe, each field best-effort.
    native = frame.payload.get("iceberg") or frame.payload.get("ducklake")
    if native:
        if native.get("snapshot_id") is not None:
            existing.snapshot_id = native["snapshot_id"]
        snapshot_at = native.get("snapshot_at")
        if snapshot_at is not None:
            existing.snapshot_at = datetime.fromisoformat(snapshot_at)
        if native.get("data_file_count") is not None:
            existing.data_file_count = native["data_file_count"]
        # Iceberg only: its REST listing carries no sizes, so the probe's sum of
        # live data-file bytes is the only figure a schema overview can use.
        if native.get("data_file_size_bytes") is not None:
            existing.size_bytes = native["data_file_size_bytes"]
        if native.get("has_deletes") is not None:
            existing.has_deletes = native["has_deletes"]
    await db.commit()


async def cancel_query(db: AsyncSession, query: Query) -> None:
    if query.agent_id:
        frame = Frame(
            type=FrameType.CANCEL_QUERY,
            payload={"query_id": str(query.id)},
        )
        # Best-effort: routes to the owning replica, or no-ops if disconnected.
        await send_to_agent(db, query.agent_id, frame.model_dump_json())
    query.status = "cancelled"
    query.finished_at = datetime.now(tz=UTC)
    await db.commit()
    if query.origin is None:
        record_query_completion("cancelled", None, None)


async def proxy_rows(
    agent: Agent,
    query: Query,
    *,
    row_offset: int | None = None,
    row_limit: int | None = None,
    token: str | None = None,
) -> httpx.Response:
    url = f"http://{agent.result_host}:{agent.result_port}/results/{query.id}.parquet"
    params: dict[str, int] = {}
    if row_offset is not None:
        params["row_offset"] = row_offset
    if row_limit is not None:
        params["row_limit"] = row_limit
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Timed here rather than at the call sites so all three of them (the rows route,
    # the table sample, the SQL-metadata reader) are covered by one instrument, and
    # so the measurement includes building the client -- which is the part worth
    # watching, since a fresh one is constructed per call.
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient() as client:
            return await client.get(url, params=params, headers=headers)
    finally:
        record_rows_proxy(time.perf_counter() - started)


async def assert_result_access(db: AsyncSession, query: Query, user_id: uuid.UUID) -> None:
    """Refuse a query's result to anyone who could not run that query now.

    A result holds the data its SQL read, so reading it needs the grants running
    it would: the scoped-catalog check is repeated for the reader, in the catalog
    and schema the run resolved names in, whoever ran it first. As at dispatch it
    is a no-op in a workspace without a scoped catalog, where any member can still
    open a colleague's result from History. Raises :class:`GrantDenied`.
    """
    from api.services.sql_sessions import service as sql_session_service

    catalogs = await resolve_workspace_catalogs(db, query.workspace_id)
    catalog, schema = query.active_catalog, query.active_schema
    prefixes: list[str] | None = None
    if query.origin == "session":
        # A null catalog means the session's context was unknown when it ran,
        # and only fully qualified names got through; the check refuses the rest.
        if query.session_id is not None and user_id == query.user_id:
            session = await db.get(SqlSession, query.session_id)
            if session is not None:
                # The author may read results of their own staged files, as at submit.
                prefixes = sql_session_service.staging_prefixes(session, catalogs)
    elif catalog is None and catalogs:
        # Rows from before the catalog was recorded resolved in the default.
        default = await get_default_catalog(db, query.workspace_id)
        catalog = default.slug if default is not None else catalogs[0].slug
    await grant_service.assert_query_access(
        db,
        query.workspace_id,
        user_id,
        query.sql,
        catalog,
        catalogs,
        active_schema=schema,
        staging_prefixes=prefixes,
    )


@dataclass
class ResultSource:
    """Where a finished query's result Parquet can be read from.

    ``inline`` holds the bytes of a result-cache entry kept in Postgres; otherwise
    the file is on ``agent``, named after ``query`` (the run that produced it, which
    for a cache hit is not the hit itself), read with ``token``.
    """

    query: Query
    inline: bytes | None = None
    agent: Agent | None = None
    token: str | None = None

    @property
    def url(self) -> str:
        assert self.agent is not None
        return (
            f"http://{self.agent.result_host}:{self.agent.result_port}"
            f"/results/{self.query.id}.parquet"
        )


async def resolve_result_source(db: AsyncSession, query: Query) -> ResultSource:
    """Find where ``query``'s result can be read, or raise the HTTPException that
    says why it cannot (410 when it is gone, 503 when its agent is unreachable).

    A result-cache hit ran nothing itself: its rows are the run it points at
    (``result_source_query_id``), read from the cache entry when the entry holds
    them, otherwise from the agent that produced them.
    """
    if query.result_source_query_id is not None:
        from api.models.result_cache import ResultCacheEntry

        inline = await db.scalar(
            select(ResultCacheEntry.inline_parquet).where(
                ResultCacheEntry.source_query_id == query.result_source_query_id,
                ResultCacheEntry.inline_parquet.is_not(None),
            )
        )
        if inline is not None:
            return ResultSource(query=query, inline=inline)
        source = await db.get(Query, query.result_source_query_id)
        if source is None or source.agent_id is None:
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Result no longer available"
            )
        query = source
    agent = (await db.execute(select(Agent).where(Agent.id == query.agent_id))).scalar_one_or_none()

    if agent is not None and agent.provider is not None:
        from api.services.compute.service import ensure_result_host, record_activity

        # An elastic agent's address is assigned after its instance is created, so it
        # can be unknown at registration time. Resolve it on first use, when the cloud
        # is certain to be able to answer.
        if agent.result_host is None:
            await ensure_result_host(db, agent)

        # Reading results counts as using the agent. The idle clock otherwise only
        # advanced on dispatch, so a user who ran a query and came back later to scroll
        # found the agent reaped and the result Parquet gone with its container --
        # results are held on the agent, not by the control plane.
        await record_activity(db, agent.id)
        await db.commit()

    if agent is None or agent.result_host is None or agent.result_port is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent result endpoint unavailable",
        )
    token = await agent_session_token(db, query.agent_id)
    return ResultSource(query=query, agent=agent, token=token)


def _raise_for_upstream(status_code: int) -> None:
    if status_code == 404:
        # The result file was swept by retention while the user was paging.
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Result no longer available")
    if status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch results from agent"
        )


async def open_result_stream(source: ResultSource) -> tuple[httpx.AsyncClient, httpx.Response]:
    """Open the agent's whole result file as a stream. The caller closes both.

    Raises the HTTPException a failed read maps to, having closed what it opened.
    No read timeout: a large result legitimately takes a while to stream.
    """
    headers = {"Authorization": f"Bearer {source.token}"} if source.token else {}
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    try:
        response = await client.send(
            client.build_request("GET", source.url, headers=headers), stream=True
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent result endpoint unavailable",
        ) from exc
    try:
        _raise_for_upstream(response.status_code)
    except HTTPException:
        await response.aclose()
        await client.aclose()
        raise
    return client, response


async def fetch_result_page(
    db: AsyncSession, query: Query, *, limit: int, offset: int = 0
) -> RowsPageOut:
    """One page of a finished query's rows, fetched from the agent holding them.

    Extracted from the rows route so the statement endpoint can serve the *first*
    page inline, saving a client round trip, without either path drifting from the
    other: both call this, so retention 410s, the elastic address resolution and
    the old-agent offset fallback behave identically wherever rows come from.

    Results live on the agent, not the control plane, so this is a proxied fetch and
    it can fail for reasons the query itself did not — the agent reaped, its result
    swept by retention. Those surface as HTTPException, which the rows route returns
    as-is and the statement route treats as "no inline page, let the client ask".

    Assumes the caller has already established that ``query`` is done, belongs to a
    workspace the caller may read, and has a ``result_path``.

    See :func:`resolve_result_source` for where a cache hit's rows come from.
    """
    total = query.row_count or 0
    column_schema = query.result_schema
    source = await resolve_result_source(db, query)
    if source.inline is not None:
        return page_from_parquet(query, source.inline, limit=limit, offset=offset)

    try:
        upstream = await proxy_rows(
            source.agent, source.query, row_offset=offset, row_limit=limit, token=source.token
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent result endpoint unavailable",
        ) from exc
    _raise_for_upstream(upstream.status_code)

    # When the agent sliced the window (X-DH-Row-Offset present) the body already
    # starts at the requested rows, so decode at offset 0. An older agent that
    # ignored the params returns the whole file -- fall back to decoding at offset.
    decode_offset = 0 if "X-DH-Row-Offset" in upstream.headers else offset
    started = time.monotonic()
    rows, columns = decode_parquet_page(upstream.content, limit, decode_offset)
    record_rows_decode(time.monotonic() - started)

    next_offset = offset + limit
    return RowsPageOut(
        rows=rows,
        columns=columns,
        cursor=str(next_offset) if next_offset < total else None,
        total=total,
        column_schema=column_schema,
    )


def page_from_parquet(query: Query, data: bytes, *, limit: int, offset: int = 0) -> RowsPageOut:
    """One page of ``query``'s rows from its whole result file, held in memory.

    For a result that is already here: a result-cache entry kept in Postgres, or
    the file the agent sent with a statement's QUERY_DONE.
    """
    total = query.row_count or 0
    started = time.monotonic()
    rows, columns = decode_parquet_page(data, limit, offset)
    record_rows_decode(time.monotonic() - started)
    next_offset = offset + limit
    return RowsPageOut(
        rows=rows,
        columns=columns,
        cursor=str(next_offset) if next_offset < total else None,
        total=total,
        column_schema=query.result_schema,
    )


async def agent_session_token(db: AsyncSession, agent_id: uuid.UUID) -> str | None:
    cred = (
        await db.execute(
            sa.select(Credential).where(
                Credential.agent_id == agent_id, Credential.kind == "agent_session"
            )
        )
    ).scalar_one_or_none()
    return cred.token if cred is not None else None


# Types whose values JSON cannot carry exactly. A JSON number is a double in most
# readers (every browser among them), which holds integers only up to 2^53 and no
# decimal fraction exactly, so values of these types are sent as strings. By type,
# not by value: a column keeps one JSON shape whatever its values are.
_EXACT_AS_TEXT = frozenset({"decimal", "bigint", "ubigint", "hugeint", "uhugeint"})


def _json_safe(value: Any) -> Any:
    """Coerce a DuckDB cell to a JSON-encodable value for RowsPageOut."""
    if isinstance(value, datetime | dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _json_value(value: Any, value_type: duckdb.DuckDBPyType | None) -> Any:
    """A cell as JSON, following its DuckDB type into lists, structs and maps so a
    nested DECIMAL or BIGINT is sent exactly too."""
    if value is None:
        return None
    tid = value_type.id if value_type is not None else None
    if tid in _EXACT_AS_TEXT:
        return str(value)
    if tid in ("list", "array"):
        element = value_type.children[0][1]
        return [_json_value(v, element) for v in value]
    if tid == "struct" and isinstance(value, dict):
        members = dict(value_type.children)
        return {k: _json_value(v, members.get(k)) for k, v in value.items()}
    if tid == "map" and isinstance(value, dict):
        (_, key_type), (_, item_type) = value_type.children
        return {_json_value(k, key_type): _json_value(v, item_type) for k, v in value.items()}
    return _json_safe(value)


# One in-memory DuckDB for every page decode. Opening and closing a database per
# page took about half of a 1000-row decode, on the event loop. Each decode reads
# through its own cursor, a separate connection to this database, because a
# connection is not safe to share between threads.
_decoder: duckdb.DuckDBPyConnection | None = None
_decoder_lock = threading.Lock()


def _decoder_cursor() -> duckdb.DuckDBPyConnection:
    global _decoder
    with _decoder_lock:
        if _decoder is None:
            _decoder = duckdb.connect()
        return _decoder.cursor()


def decode_parquet_page(
    content: bytes, limit: int, offset: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Decode a page of a Parquet result (bytes) into JSON-safe rows + columns."""
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        conn = _decoder_cursor()
        try:
            cur = conn.execute(
                "SELECT * FROM read_parquet(?) LIMIT ? OFFSET ?", [path, limit, offset]
            )
            columns = [d[0] for d in cur.description]
            types = [d[1] for d in cur.description]
            rows = [
                {
                    col: _json_value(val, typ)
                    for col, val, typ in zip(columns, record, types, strict=True)
                }
                for record in cur.fetchall()
            ]
            return rows, columns
        finally:
            conn.close()
    finally:
        os.unlink(path)


async def pick_agent_for(
    db: AsyncSession,
    workspace: Workspace,
    *,
    principal_id: uuid.UUID | None = None,
    for_session: bool = False,
) -> Agent | None:
    """A connected agent whose capabilities support *every* backend kind across
    the workspace's catalogs (all are attached on each query).

    When ``principal_id`` is given, only agents that principal may ``use`` are
    considered. Pass it wherever the selection is made on someone's behalf:
    without it a caller denied agent A could simply omit ``agent_id`` and be routed
    to A anyway. ``None`` means a system actor with no principal to check against
    (the maintenance scanner), which is deliberately unfiltered.

    Nobody named an agent, so nobody chose a runtime: the deployment's default
    runtime is preferred, then other generally available ones, then deprecated
    ones. An agent on a beta runtime is never picked here — only work that names
    it runs there. ``for_session`` also skips agents whose configuration lock
    failed, which cannot hold a SQL session.
    """
    connected = await connected_agent_ids(db)
    if not connected:
        return None
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    # The agent needs the extensions for every catalog's kind *and* backend.
    pairs = {(c.kind, c.storage_backend.kind) for c in catalogs} or {
        ("iceberg_polaris", "object_store")
    }
    agents = list(
        (await db.execute(sa.select(Agent).where(Agent.id.in_([uuid.UUID(c) for c in connected]))))
        .scalars()
        .all()
    )
    if principal_id is not None:
        agents = await agent_access.usable_agents(db, principal_id, agents)
    compatible = [
        agent
        for agent in agents
        if all(
            agent_supports_catalog(agent.capabilities, catalog_kind, backend_kind)
            for catalog_kind, backend_kind in pairs
        )
        and not (for_session and (agent.capabilities or {}).get("sandbox") == "failed")
    ]
    ranked = [
        (rank, agent)
        for agent in compatible
        if (rank := runtime_service.auto_pick_rank(agent)) is not None
    ]
    if not ranked:
        if compatible:
            # Otherwise background work — maintenance, say — would stop without a
            # trace whenever the only agents up are beta or unsupported ones.
            logger.info(
                "No agent to auto-pick for workspace %s: %d compatible agent(s), all on "
                "beta or unsupported runtimes",
                workspace.slug,
                len(compatible),
            )
        return None
    return min(ranked, key=lambda ranked_agent: ranked_agent[0])[1]


class ComputeStarting(Exception):
    """No agent could take an internal run in time; compute is starting for it."""


async def run_internal_query(
    db: AsyncSession,
    *,
    workspace: Workspace,
    user_id: uuid.UUID,
    sql: str,
    origin: str,
    active_catalog: str | None = None,
    agent: Agent | None = None,
    wait_s: float,
) -> Query:
    """Run platform SQL on the caller's behalf and wait up to ``wait_s`` for it.

    Like :func:`run_sync_query`, but when no agent is given or connected and
    elastic compute is on, it starts compute the way a pool run does: the run is
    parked with no agent and the provisioned agent picks it up when it registers
    (``compute.service.bind_queued_work`` binds ``origin`` alongside pool runs).
    If the wait runs out while the run is still parked it is cancelled and
    :class:`ComputeStarting` is raised; the compute keeps starting, so a retry
    lands on it. Grant and dispatch errors propagate as from ``dispatch_query``.
    """
    from api.config import settings
    from api.services.compute import service as compute_service

    if agent is None:
        agent = await pick_agent_for(db, workspace, principal_id=user_id)
    if agent is not None:
        return await run_sync_query(
            db,
            workspace=workspace,
            agent=agent,
            user_id=user_id,
            sql=sql,
            origin=origin,
            active_catalog=active_catalog,
            timeout_s=wait_s,
        )
    if not settings.elastic_compute_enabled:
        raise AgentUnavailable("No compatible agent is connected")

    query = Query(
        workspace_id=workspace.id,
        agent_id=None,
        user_id=user_id,
        sql=sql,
        status="queued",
        origin=origin,
        active_catalog=active_catalog,
    )
    db.add(query)
    await db.flush()
    await compute_service.ensure_agent(db, await compute_service.resolve_pool_key(db, workspace))
    await db.commit()

    deadline = asyncio.get_event_loop().time() + wait_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.2)
        await db.refresh(query)
        if query.status in {"done", "failed", "cancelled"}:
            return query
    # Abandon the run only if no agent took it meanwhile: a bound run is the
    # caller's to collect, and cancelling it here would race its dispatch.
    abandoned = await db.execute(
        sa.update(Query)
        .where(Query.id == query.id, Query.agent_id.is_(None))
        .values(status="cancelled", finished_at=datetime.now(tz=UTC))
    )
    await db.commit()
    if abandoned.rowcount:
        raise ComputeStarting("Compute is starting; retry shortly")
    await db.refresh(query)
    return query


async def run_sync_query(
    db: AsyncSession,
    *,
    workspace: Workspace,
    agent: Agent,
    user_id: uuid.UUID,
    sql: str,
    origin: str | None = None,
    active_catalog: str | None = None,
    stats_for: dict[str, str] | None = None,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.2,
) -> Query:
    """Dispatch a query and block until it reaches a terminal state (or timeout).

    Used by the synchronous table-sample preview. The QUERY_DONE frame is applied
    by the websocket handler in a separate session, so we poll this session's view.
    """
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=user_id,
        sql=sql,
        status="queued",
        origin=origin,
    )
    db.add(query)
    await db.flush()
    await dispatch_query(db, query, active_catalog=active_catalog, stats_for=stats_for)

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval_s)
        await db.refresh(query)
        if query.status in {"done", "failed", "cancelled"}:
            break
    return query
