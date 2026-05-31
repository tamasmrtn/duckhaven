"""Schemas + tables endpoints (M3, G-D8-b, G-D9-a).

All endpoints are workspace-scoped and gated by `assert_workspace_member`.
list operations require `reader`; creates require `writer`. UC is the
authority — we never write schema/table state into pg.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_cred_cache, get_current_user, get_db, get_uc_client
from api.models.agent import Agent
from api.models.storage_backend import StorageBackend
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.models.workspace import Workspace
from api.schemas.catalog import (
    AllowedColumnType,
    CatalogSchemaCreate,
    CatalogSchemaOut,
    ColumnSpec,
    TableColumnOut,
    TableCreate,
    TableOut,
)
from api.schemas.query import RowsPageOut
from api.services import query as query_service
from api.services.uc_credentials import CredCache
from api.services.unity_catalog import (
    UCClient,
    UCConflictError,
    UCNotFoundError,
    UCTable,
)
from api.services.workspace import assert_workspace_member, ensure_uc_catalog, get_workspace

logger = logging.getLogger(__name__)


@dataclass
class _TableMeta:
    """Resolved control-plane metadata for a single table (display-ready)."""

    row_count: int | None
    size_bytes: int | None
    owner: str | None
    last_write_at: datetime | None
    last_write_by: str | None
    last_write_agent: str | None


async def _load_table_meta(
    db: AsyncSession, workspace_id: uuid.UUID, schema_name: str
) -> dict[str, _TableMeta]:
    """Load TableMetadata for a schema, resolving owner/writer/agent to display names."""
    rows = list(
        (
            await db.execute(
                select(TableMetadata).where(
                    TableMetadata.workspace_id == workspace_id,
                    TableMetadata.schema_name == schema_name,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return {}

    user_ids = {r.owner_id for r in rows if r.owner_id} | {
        r.last_write_by_id for r in rows if r.last_write_by_id
    }
    agent_ids = {r.last_write_agent_id for r in rows if r.last_write_agent_id}
    users: dict[uuid.UUID, str] = {}
    if user_ids:
        users = {
            u.id: u.name
            for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        }
    agents: dict[uuid.UUID, str] = {}
    if agent_ids:
        agents = {
            a.id: a.name
            for a in (await db.execute(select(Agent).where(Agent.id.in_(agent_ids))))
            .scalars()
            .all()
        }

    return {
        r.table_name: _TableMeta(
            row_count=r.row_count,
            size_bytes=r.size_bytes,
            owner=users.get(r.owner_id) if r.owner_id else None,
            last_write_at=r.last_write_at,
            last_write_by=users.get(r.last_write_by_id) if r.last_write_by_id else None,
            last_write_agent=agents.get(r.last_write_agent_id) if r.last_write_agent_id else None,
        )
        for r in rows
    }


router = APIRouter(prefix="/workspaces/{ws}/schemas")


# Map the small set of allowed scalar types to the verbose UC column shape.
_TYPE_TO_UC: dict[AllowedColumnType, tuple[str, str]] = {
    "INTEGER": ("INT", "int"),
    "BIGINT": ("LONG", "bigint"),
    "DOUBLE": ("DOUBLE", "double"),
    "VARCHAR": ("STRING", "string"),
    "BOOLEAN": ("BOOLEAN", "boolean"),
    "DATE": ("DATE", "date"),
    "TIMESTAMP": ("TIMESTAMP", "timestamp"),
    "DECIMAL": ("DECIMAL", "decimal"),
}


def _column_for_uc(spec: ColumnSpec, position: int) -> dict[str, object]:
    type_name, type_text = _TYPE_TO_UC[spec.type]
    return {
        "name": spec.name,
        "type_text": type_text,
        "type_name": type_name,
        "type_json": "",
        "type_precision": 0,
        "type_scale": 0,
        "type_interval_type": None,
        "position": position,
        "nullable": spec.nullable,
        "comment": None,
    }


def _table_to_out(
    table: UCTable, workspace_id: uuid.UUID, meta: _TableMeta | None = None
) -> TableOut:
    props = table.properties or {}
    return TableOut(
        name=table.name,
        schema_name=table.schema_name,
        catalog_name=table.catalog_name,
        workspace_id=str(workspace_id),
        table_type=table.table_type,
        data_source_format=table.data_source_format,
        format=table.data_source_format,
        storage_location=table.storage_location,
        columns=[
            TableColumnOut(
                name=c.name,
                type_text=c.type_text,
                type_name=c.type_name,
                type=c.type_text,
                position=c.position,
                nullable=c.nullable,
            )
            for c in (table.columns or [])
        ],
        properties=props,
        table_id=table.table_id,
        catalog_commits="delta.feature.catalogManaged" in props,
        row_count=meta.row_count if meta else None,
        size_bytes=meta.size_bytes if meta else None,
        owner=meta.owner if meta else None,
        last_write_at=meta.last_write_at if meta else None,
        last_write_by=meta.last_write_by if meta else None,
        last_write_agent=meta.last_write_agent if meta else None,
    )


async def _resolve_workspace(ws: str, user: User, db: AsyncSession, min_role: str) -> Workspace:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role=min_role)
    return workspace


async def _backend_root_uri(db: AsyncSession, workspace: Workspace) -> str:
    backend = await db.get(StorageBackend, workspace.storage_backend_id)
    if backend is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Workspace points to a missing storage backend",
        )
    return backend.root_uri


@router.get("", response_model=list[CatalogSchemaOut])
async def list_schemas(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
) -> list[CatalogSchemaOut]:
    workspace = await _resolve_workspace(ws, user, db, min_role="reader")
    await ensure_uc_catalog(uc, workspace.slug)
    schemas = await uc.list_schemas(workspace.slug)
    return [
        CatalogSchemaOut(name=s.name, catalog_name=s.catalog_name, workspace_id=str(workspace.id))
        for s in schemas
    ]


@router.post("", response_model=CatalogSchemaOut, status_code=status.HTTP_201_CREATED)
async def create_schema(
    ws: str,
    body: CatalogSchemaCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
) -> CatalogSchemaOut:
    workspace = await _resolve_workspace(ws, user, db, min_role="writer")
    await ensure_uc_catalog(uc, workspace.slug)
    try:
        sc = await uc.create_schema(workspace.slug, body.name)
    except UCConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CatalogSchemaOut(
        name=sc.name, catalog_name=sc.catalog_name, workspace_id=str(workspace.id)
    )


@router.get("/{schema}/tables", response_model=list[TableOut])
async def list_tables(
    ws: str,
    schema: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
) -> list[TableOut]:
    workspace = await _resolve_workspace(ws, user, db, min_role="reader")
    tables = await uc.list_tables(workspace.slug, schema)
    meta = await _load_table_meta(db, workspace.id, schema)
    return [_table_to_out(t, workspace.id, meta.get(t.name)) for t in tables]


@router.get("/{schema}/tables/{table}", response_model=TableOut)
async def get_table(
    ws: str,
    schema: str,
    table: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
) -> TableOut:
    workspace = await _resolve_workspace(ws, user, db, min_role="reader")
    try:
        t = await uc.get_table(workspace.slug, schema, table)
    except UCNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    meta = await _load_table_meta(db, workspace.id, schema)
    return _table_to_out(t, workspace.id, meta.get(t.name))


@router.post(
    "/{schema}/tables",
    response_model=TableOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_table(
    ws: str,
    schema: str,
    body: TableCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
) -> TableOut:
    workspace = await _resolve_workspace(ws, user, db, min_role="writer")
    root_uri = await _backend_root_uri(db, workspace)
    # Storage layout matches §5: <root>/<schema>/<table>/ with a trailing slash.
    storage_location = f"{root_uri.rstrip('/')}/{schema}/{body.name}/"
    columns = [_column_for_uc(spec, idx) for idx, spec in enumerate(body.columns)]

    await ensure_uc_catalog(uc, workspace.slug)
    try:
        t = await uc.create_table(
            catalog=workspace.slug,
            schema=schema,
            name=body.name,
            columns=columns,
            storage_location=storage_location,
            data_source_format="DELTA",
            table_type="MANAGED",
            properties={"delta.feature.catalogManaged": "supported"},
        )
    except UCConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Record ownership + initial (empty) stats. The creator is owner + last writer.
    db.add(
        TableMetadata(
            workspace_id=workspace.id,
            schema_name=schema,
            table_name=body.name,
            owner_id=user.id,
            row_count=0,
            size_bytes=0,
            last_write_at=datetime.now(tz=UTC),
            last_write_by_id=user.id,
        )
    )
    await db.commit()
    meta = await _load_table_meta(db, workspace.id, schema)
    return _table_to_out(t, workspace.id, meta.get(body.name))


@router.delete("/{schema}/tables/{table}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_table(
    ws: str,
    schema: str,
    table: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
) -> None:
    workspace = await _resolve_workspace(ws, user, db, min_role="writer")
    try:
        await uc.delete_table(workspace.slug, schema, table)
    except UCNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc


# Rows previewed in the table-detail view.
_SAMPLE_LIMIT = 20


@router.get("/{schema}/tables/{table}/sample", response_model=RowsPageOut)
async def sample_table(
    ws: str,
    schema: str,
    table: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
    cred_cache: CredCache = Depends(get_cred_cache),
) -> RowsPageOut:
    workspace = await _resolve_workspace(ws, user, db, min_role="reader")
    try:
        await uc.get_table(workspace.slug, schema, table)
    except UCNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc

    agent = await query_service.pick_agent_for(db, workspace)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No compatible agent is connected to preview this table.",
        )

    sql = f'SELECT * FROM "{schema}"."{table}" LIMIT {_SAMPLE_LIMIT}'
    query = await query_service.run_sync_query(
        db,
        workspace=workspace,
        agent=agent,
        user_id=user.id,
        sql=sql,
        uc=uc,
        cred_cache=cred_cache,
        origin="sample",
        stats_for={"schema": schema, "table": table},
    )
    if query.status != "done" or query.result_path is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Table preview did not complete in time.",
        )

    token = await query_service.agent_session_token(db, agent.id)
    upstream = await query_service.proxy_rows(agent, query, token=token)
    if upstream.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch preview rows."
        )
    rows, columns = query_service.decode_parquet_page(upstream.content, _SAMPLE_LIMIT, 0)
    return RowsPageOut(rows=rows, columns=columns, cursor=None, total=query.row_count or len(rows))
