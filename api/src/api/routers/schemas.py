"""Schemas + tables endpoints (M3, G-D8-b, G-D9-a).

All endpoints are workspace-scoped and gated by `assert_workspace_member`.
list operations require `reader`; creates require `writer`. UC is the
authority — we never write schema/table state into pg.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_uc_client
from api.models.storage_backend import StorageBackend
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
from api.services.unity_catalog import (
    UCClient,
    UCConflictError,
    UCNotFoundError,
    UCTable,
)
from api.services.workspace import assert_workspace_member, ensure_uc_catalog, get_workspace

logger = logging.getLogger(__name__)


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


def _table_to_out(table: UCTable) -> TableOut:
    return TableOut(
        name=table.name,
        schema_name=table.schema_name,
        catalog_name=table.catalog_name,
        table_type=table.table_type,
        data_source_format=table.data_source_format,
        storage_location=table.storage_location,
        columns=[
            TableColumnOut(
                name=c.name,
                type_text=c.type_text,
                type_name=c.type_name,
                position=c.position,
                nullable=c.nullable,
            )
            for c in (table.columns or [])
        ],
        properties=table.properties or {},
        table_id=table.table_id,
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
    return [CatalogSchemaOut(name=s.name, catalog_name=s.catalog_name) for s in schemas]


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
    return CatalogSchemaOut(name=sc.name, catalog_name=sc.catalog_name)


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
    return [_table_to_out(t) for t in tables]


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
    return _table_to_out(t)


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
    return _table_to_out(t)


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
