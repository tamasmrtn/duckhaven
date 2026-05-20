import uuid
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.workspace import Workspace
from api.services.agent_registry import registry
from api.services.uc_credentials import CredCache, vend_workspace_creds
from api.services.unity_catalog import UCClient
from duckhaven_shared.protocol import Frame, FrameType


async def dispatch_query(
    db: AsyncSession,
    query: Query,
    *,
    uc: UCClient,
    cred_cache: CredCache,
    memory_limit_gb: float = 6.0,
    timeout_s: float = 600.0,
) -> None:
    if query.agent_id is None or registry.get(query.agent_id) is None:
        raise ValueError("Agent not connected")

    workspace = await db.get(Workspace, query.workspace_id)
    if workspace is None:
        raise ValueError("Workspace missing for query")
    backend = await db.get(StorageBackend, workspace.storage_backend_id)
    if backend is None:
        raise ValueError("Storage backend missing for workspace")

    creds = await cred_cache.get_or_fetch(
        f"{query.agent_id}:{workspace.slug}",
        lambda: vend_workspace_creds(uc, workspace.slug, backend.kind),
    )

    payload: dict[str, object] = {
        "query_id": str(query.id),
        "sql": query.sql,
        "memory_limit_gb": memory_limit_gb,
        "timeout_s": timeout_s,
        "workspace": {"slug": workspace.slug},
        "backend": {"kind": backend.kind, "root_uri": backend.root_uri},
        "unity_catalog": {"endpoint": settings.uc_base_url},
    }
    if creds is not None:
        payload["storage_credentials"] = creds.to_payload()

    frame = Frame(type=FrameType.DISPATCH_QUERY, payload=payload)
    await registry.send(query.agent_id, frame.model_dump_json())
    query.status = "running"
    await db.commit()


async def handle_agent_frame(db: AsyncSession, frame: Frame) -> None:
    query_id = uuid.UUID(frame.payload["query_id"])
    if frame.type == FrameType.QUERY_DONE:
        await db.execute(
            sa.update(Query)
            .where(Query.id == query_id)
            .values(
                status=frame.payload.get("status", "done"),
                row_count=frame.payload.get("row_count"),
                duration_ms=frame.payload.get("duration_ms"),
                error=frame.payload.get("error"),
                result_path=frame.payload.get("result_path"),
                finished_at=datetime.now(tz=UTC),
            )
        )
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


async def proxy_rows(agent: Agent, query: Query, range_header: str | None = None) -> httpx.Response:
    url = f"http://{agent.result_host}:{agent.result_port}/results/{query.id}.parquet"
    headers: dict[str, str] = {}
    if range_header:
        headers["Range"] = range_header
    async with httpx.AsyncClient() as client:
        return await client.get(url, headers=headers)
