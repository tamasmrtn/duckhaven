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

from api.metrics import record_query_completion, record_query_submitted
from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.table_metadata import TableMetadata
from api.models.user import Credential
from api.models.workspace import Workspace
from api.services.agent_capabilities import agent_supports_backend
from api.services.agent_dispatch import (
    connected_agent_ids,
    is_agent_connected,
    send_to_agent,
)
from api.services.workspace import (
    DEFAULT_SCHEMA,
    get_default_catalog,
    resolve_workspace_catalogs,
)
from duckhaven_shared.protocol import Frame, FrameType


async def dispatch_query(
    db: AsyncSession,
    query: Query,
    *,
    timeout_s: float = 600.0,
    active_catalog: str | None = None,
    stats_for: dict[str, str] | None = None,
    health_for: dict[str, object] | None = None,
) -> None:
    if query.agent_id is None or not await is_agent_connected(db, query.agent_id):
        raise ValueError("Agent not connected")

    workspace = await db.get(Workspace, query.workspace_id)
    if workspace is None:
        raise ValueError("Workspace missing for query")
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    if not catalogs:
        raise ValueError("Workspace has no catalogs attached")

    # Eager multi-attach: the agent ATTACHes every catalog bound to the
    # workspace (each under its slug) and `USE`s the active one for unqualified
    # names. The control plane vends nothing — the agent's own config supplies
    # the Polaris endpoint + client creds.
    if active_catalog is None:
        default = await get_default_catalog(db, workspace.id)
        active_catalog = default.slug if default is not None else catalogs[0].slug
    payload: dict[str, object] = {
        "query_id": str(query.id),
        "sql": query.sql,
        "timeout_s": timeout_s,
        "active_catalog": active_catalog,
        "catalogs": [
            {
                "slug": c.slug,
                "polaris_name": c.polaris_name,
                "backend": {
                    "kind": c.storage_backend.kind,
                    "root_uri": c.storage_backend.root_uri,
                },
                "default_schema": DEFAULT_SCHEMA,
            }
            for c in catalogs
        ],
    }
    if stats_for is not None:
        # Ask the agent to also compute true table stats for this table.
        payload["stats_for"] = stats_for
    if health_for is not None:
        # Ask the agent to run the maintenance health probe for this table.
        payload["health_for"] = health_for

    frame = Frame(type=FrameType.DISPATCH_QUERY, payload=payload)
    if not await send_to_agent(db, query.agent_id, frame.model_dump_json()):
        # The socket vanished between the presence check and the send, or its
        # owning replica is unreachable. Fail fast so the caller surfaces it.
        raise ValueError("Agent not connected")
    # Status stays "queued" until the agent admits the query and emits
    # QUERY_PROGRESS; the agent may hold it in its admission queue first.
    if query.origin is None:
        record_query_submitted()
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
        status_val = frame.payload.get("status", "done")
        await db.execute(
            sa.update(Query)
            .where(Query.id == query_id)
            .values(
                status=status_val,
                row_count=frame.payload.get("row_count"),
                duration_ms=frame.payload.get("duration_ms"),
                result_bytes=frame.payload.get("result_bytes"),
                error=frame.payload.get("error"),
                result_path=frame.payload.get("result_path"),
                profile=frame.payload.get("profile"),
                finished_at=datetime.now(tz=UTC),
            )
        )
        await db.commit()
        query = await db.get(Query, query_id)
        if query is not None and query.origin is None:
            record_query_completion(
                status_val,
                frame.payload.get("duration_ms"),
                frame.payload.get("result_bytes"),
            )
        if status_val == "done":
            await _upsert_table_stats(db, query_id, frame)
            health = frame.payload.get("health")
            if health and query is not None:
                from api.services.maintenance.ingest import record_health_sample

                await record_health_sample(db, query, health)


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
    async with httpx.AsyncClient() as client:
        return await client.get(url, params=params, headers=headers)


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
    """A connected agent whose capabilities support *every* backend kind across
    the workspace's catalogs (all are attached on each query)."""
    connected = await connected_agent_ids(db)
    if not connected:
        return None
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    kinds = {c.storage_backend.kind for c in catalogs} or {"object_store"}
    agents = (
        (await db.execute(sa.select(Agent).where(Agent.id.in_([uuid.UUID(c) for c in connected]))))
        .scalars()
        .all()
    )
    for agent in agents:
        if all(agent_supports_backend(agent.capabilities, kind) for kind in kinds):
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
