import asyncio
import datetime as dt
import os
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import duckdb
import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.table_metadata import TableMetadata
from api.models.user import Credential
from api.models.workspace import Workspace
from api.services.agent_capabilities import agent_supports_backend
from api.services.agent_registry import registry
from api.services.workspace import DEFAULT_SCHEMA
from duckhaven_shared.protocol import Frame, FrameType


async def dispatch_query(
    db: AsyncSession,
    query: Query,
    *,
    memory_limit_gb: float = 6.0,
    timeout_s: float = 600.0,
    stats_for: dict[str, str] | None = None,
) -> None:
    if query.agent_id is None or registry.get(query.agent_id) is None:
        raise ValueError("Agent not connected")

    # Clamp the requested memory to the agent's advertised ceiling so the
    # picker's numbers and the dispatched cap agree (the agent enforces its
    # own hard ceiling regardless; this is fast feedback). G-D2-b.
    agent = await db.get(Agent, query.agent_id)
    if agent is not None and agent.capabilities:
        cap = agent.capabilities.get("memory_limit_gb")
        if cap:
            memory_limit_gb = min(memory_limit_gb, float(cap))

    workspace = await db.get(Workspace, query.workspace_id)
    if workspace is None:
        raise ValueError("Workspace missing for query")
    backend = await db.get(StorageBackend, workspace.storage_backend_id)
    if backend is None:
        raise ValueError("Storage backend missing for workspace")

    # The agent attaches Polaris from its own config (endpoint + client creds)
    # and uses the workspace slug as the warehouse; the control plane vends
    # nothing and passes no catalog token.
    payload: dict[str, object] = {
        "query_id": str(query.id),
        "sql": query.sql,
        "memory_limit_gb": memory_limit_gb,
        "timeout_s": timeout_s,
        "workspace": {"slug": workspace.slug, "default_schema": DEFAULT_SCHEMA},
        "backend": {"kind": backend.kind, "root_uri": backend.root_uri},
    }
    if stats_for is not None:
        # Ask the agent to also compute true table stats for this table.
        payload["stats_for"] = stats_for

    frame = Frame(type=FrameType.DISPATCH_QUERY, payload=payload)
    await registry.send(query.agent_id, frame.model_dump_json())
    query.status = "running"
    await db.commit()


async def handle_agent_frame(db: AsyncSession, frame: Frame) -> None:
    query_id = uuid.UUID(frame.payload["query_id"])
    if frame.type == FrameType.QUERY_PROGRESS:
        progress = {k: v for k, v in frame.payload.items() if k != "query_id"}
        await db.execute(
            sa.update(Query)
            .where(Query.id == query_id)
            .values(status="running", progress=progress or None)
        )
        await db.commit()
        return
    if frame.type == FrameType.QUERY_DONE:
        await db.execute(
            sa.update(Query)
            .where(Query.id == query_id)
            .values(
                status=frame.payload.get("status", "done"),
                row_count=frame.payload.get("row_count"),
                duration_ms=frame.payload.get("duration_ms"),
                result_bytes=frame.payload.get("result_bytes"),
                error=frame.payload.get("error"),
                result_path=frame.payload.get("result_path"),
                finished_at=datetime.now(tz=UTC),
            )
        )
        await db.commit()
        if frame.payload.get("status", "done") == "done":
            await _upsert_table_stats(db, query_id, frame)


async def _upsert_table_stats(db: AsyncSession, query_id: uuid.UUID, frame: Frame) -> None:
    """Persist agent-computed table row/size stats reported alongside a query."""
    stats = frame.payload.get("stats_table")
    if not stats:
        return
    schema_name = stats.get("schema")
    table_name = stats.get("table")
    if not schema_name or not table_name:
        return
    query = await db.get(Query, query_id)
    if query is None:
        return
    existing = (
        await db.execute(
            sa.select(TableMetadata).where(
                TableMetadata.workspace_id == query.workspace_id,
                TableMetadata.schema_name == schema_name,
                TableMetadata.table_name == table_name,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = TableMetadata(
            workspace_id=query.workspace_id,
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

    # Iceberg-native metadata from the agent probe (each field best-effort).
    iceberg = frame.payload.get("iceberg")
    if iceberg:
        if iceberg.get("snapshot_id") is not None:
            existing.snapshot_id = iceberg["snapshot_id"]
        snapshot_at = iceberg.get("snapshot_at")
        if snapshot_at is not None:
            existing.snapshot_at = datetime.fromisoformat(snapshot_at)
        if iceberg.get("data_file_count") is not None:
            existing.data_file_count = iceberg["data_file_count"]
        if iceberg.get("has_deletes") is not None:
            existing.has_deletes = iceberg["has_deletes"]
    await db.commit()


async def cancel_query(db: AsyncSession, query: Query) -> None:
    if query.agent_id and registry.get(query.agent_id):
        frame = Frame(
            type=FrameType.CANCEL_QUERY,
            payload={"query_id": str(query.id)},
        )
        await registry.send(query.agent_id, frame.model_dump_json())
    query.status = "cancelled"
    query.finished_at = datetime.now(tz=UTC)
    await db.commit()


async def proxy_rows(
    agent: Agent,
    query: Query,
    range_header: str | None = None,
    *,
    token: str | None = None,
) -> httpx.Response:
    url = f"http://{agent.result_host}:{agent.result_port}/results/{query.id}.parquet"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if range_header:
        headers["Range"] = range_header
    async with httpx.AsyncClient() as client:
        return await client.get(url, headers=headers)


async def agent_session_token(db: AsyncSession, agent_id: uuid.UUID) -> str | None:
    cred = (
        await db.execute(
            sa.select(Credential).where(
                Credential.agent_id == agent_id, Credential.kind == "agent_session"
            )
        )
    ).scalar_one_or_none()
    return cred.token if cred is not None else None


def _json_safe(value: Any) -> Any:
    """Coerce a DuckDB cell to a JSON-encodable value for RowsPageOut."""
    if isinstance(value, datetime | dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def decode_parquet_page(
    content: bytes, limit: int, offset: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Decode a page of a Parquet result (bytes) into JSON-safe rows + columns."""
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        conn = duckdb.connect()
        try:
            cur = conn.execute(
                "SELECT * FROM read_parquet(?) LIMIT ? OFFSET ?", [path, limit, offset]
            )
            columns = [d[0] for d in cur.description]
            rows = [
                {col: _json_safe(val) for col, val in zip(columns, record, strict=True)}
                for record in cur.fetchall()
            ]
            return rows, columns
        finally:
            conn.close()
    finally:
        os.unlink(path)


async def pick_agent_for(db: AsyncSession, workspace: Workspace) -> Agent | None:
    """A connected agent whose capabilities support the workspace's backend."""
    connected = registry.connected_ids()
    if not connected:
        return None
    backend = await db.get(StorageBackend, workspace.storage_backend_id)
    kind = backend.kind if backend is not None else "local_fs"
    agents = (
        (await db.execute(sa.select(Agent).where(Agent.id.in_([uuid.UUID(c) for c in connected]))))
        .scalars()
        .all()
    )
    for agent in agents:
        if agent_supports_backend(agent.capabilities, kind):
            return agent
    return None


async def run_sync_query(
    db: AsyncSession,
    *,
    workspace: Workspace,
    agent: Agent,
    user_id: uuid.UUID,
    sql: str,
    origin: str | None = None,
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
    await dispatch_query(db, query, stats_for=stats_for)

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval_s)
        await db.refresh(query)
        if query.status in {"done", "failed", "cancelled"}:
            break
    return query
