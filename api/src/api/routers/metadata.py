"""The grant-filtered metadata report: a workspace's catalogs, schemas, tables and,
on request, their columns in DuckDB types.

Built for drivers and BI gateways, which answer their catalog listings from it.
Names come from the catalog backends with no agent involved, redacted by grant
exactly as the catalog tree is. Column types come from ``DESCRIBE`` on an agent,
because only the engine spells them the way queries see them (Iceberg's ``long``
is DuckDB's ``BIGINT``); tables are described in batches, one query each.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from api.config import settings
from api.deps import get_current_user, get_db, get_polaris_client
from api.models.catalog import Catalog
from api.models.user import User
from api.models.workspace import Workspace
from api.schemas.metadata import (
    MetadataCatalogOut,
    MetadataColumnOut,
    MetadataSchemaOut,
    MetadataTableOut,
    WorkspaceMetadataOut,
)
from api.services import catalog_tree
from api.services import grants as grant_service
from api.services import query as query_service
from api.services.grants import GrantDenied
from api.services.polaris import PolarisClient
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)

router = APIRouter(prefix="/workspaces")

# Tables described per agent query. Each one costs the agent a metadata load, so
# a batch stays small enough to finish well inside one wait.
_BATCH_TABLES = 100
# Rows read per page when collecting a batch's DESCRIBE output.
_PAGE_ROWS = 1000


@router.get(
    "/{workspace}/metadata",
    response_model=WorkspaceMetadataOut,
    responses={
        422: {"description": "More tables than one request may describe; narrow the filter."},
        504: {"description": "The agent describing the tables did not answer in time."},
    },
)
async def get_workspace_metadata(
    ws: Annotated[str, Path(alias="workspace")],
    catalog: Annotated[str | None, Query(description="Only this catalog.")] = None,
    schema: Annotated[str | None, Query(description="Only this schema.")] = None,
    table: Annotated[str | None, Query(description="Only this table.")] = None,
    columns: Annotated[
        bool, Query(description="Describe each table's columns, which needs an agent.")
    ] = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> WorkspaceMetadataOut:
    """Every catalog, schema and table the caller may see in the workspace, with
    each table's columns in DuckDB types when `columns=true`.

    Filtered by grant: an object the caller holds no tier on is left out, as in
    the catalog tree. Names need no compute. Columns are described by an agent;
    with none connected and elastic compute on, compute is started and the call
    waits, answering 503 `compute_starting` if it is not up in time. At most
    `METADATA_COLUMNS_MAX_TABLES` tables are described per request."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    if catalog is not None:
        catalogs = [c for c in catalogs if c.slug == catalog]
        if not catalogs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Catalog not attached"
            )

    listed = await catalog_tree.list_catalogs(catalogs, polaris, with_tables=True, schema=schema)
    report: list[MetadataCatalogOut] = []
    for entry in listed:
        cat = entry.catalog
        scoped = await grant_service.is_scoped(db, workspace.id, cat)
        schema_names = [s.name for s in entry.schemas]
        if scoped:
            visible = await grant_service.visible_schemas(
                db, workspace.id, cat, user.id, schema_names
            )
            schema_names = [n for n in schema_names if n in visible]
        schemas_out = []
        for schema_name in schema_names:
            tables = [
                t for t in entry.tables.get(schema_name, []) if table is None or t.name == table
            ]
            if scoped and tables:
                visible = await grant_service.visible_tables(
                    db, workspace.id, cat, user.id, schema_name, [t.name for t in tables]
                )
                tables = [t for t in tables if t.name in visible]
            schemas_out.append(
                MetadataSchemaOut(
                    name=schema_name,
                    tables=[MetadataTableOut(name=t.name, table_type=t.table_type) for t in tables],
                )
            )
        report.append(
            MetadataCatalogOut(
                name=cat.slug,
                kind=cat.kind,
                access_mode="scoped" if scoped else "open",
                schemas=schemas_out,
            )
        )

    if columns:
        count = sum(len(s.tables) for c in report for s in c.schemas)
        if count > settings.metadata_columns_max_tables:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "too_many_tables",
                    "detail": (
                        f"{count} tables match; columns can be described for at most "
                        f"{settings.metadata_columns_max_tables} per request. "
                        "Narrow it with catalog or schema."
                    ),
                },
            )
        by_slug = {c.slug: c for c in catalogs}
        for cat_out in report:
            await _fill_columns(db, workspace, user, by_slug[cat_out.name], cat_out)
    return WorkspaceMetadataOut(catalogs=report)


async def _fill_columns(
    db: AsyncSession, workspace: Workspace, user: User, cat: Catalog, cat_out: MetadataCatalogOut
) -> None:
    """Describe every listed table of one catalog, a batch per agent query."""
    targets = [(s.name, t) for s in cat_out.schemas for t in s.tables]
    for start in range(0, len(targets), _BATCH_TABLES):
        batch = targets[start : start + _BATCH_TABLES]
        described = await _describe(db, workspace, user, cat, [(s, t.name) for s, t in batch])
        if described is None:
            # One table the batch could not bind (dropped since it was listed, or
            # unreadable) fails them all: describe each alone so the rest still answer.
            described = {}
            for schema_name, t in batch:
                alone = await _describe(db, workspace, user, cat, [(schema_name, t.name)])
                described.update(alone or {})
        for schema_name, t in batch:
            t.columns = described.get((schema_name, t.name))


async def _describe(
    db: AsyncSession,
    workspace: Workspace,
    user: User,
    cat: Catalog,
    tables: list[tuple[str, str]],
) -> dict[tuple[str, str], list[MetadataColumnOut]] | None:
    """The columns of ``tables``, from one DESCRIBE query; ``None`` if it failed.

    Tables are numbered rather than named in the result: a table name in a string
    literal would make the query look like a catalog listing to the attach logic.
    """

    def ident(name: str) -> str:
        return exp.to_identifier(name, quoted=True).sql(dialect="duckdb")

    branches = [
        f"SELECT {i} AS i, row_number() OVER () AS pos, column_name, column_type, "
        f'"null" AS nullable FROM (DESCRIBE {ident(cat.slug)}.{ident(s)}.{ident(t)})'
        for i, (s, t) in enumerate(tables)
    ]
    sql = (
        "SELECT i, column_name, column_type, nullable FROM ("
        + " UNION ALL ".join(branches)
        + ") ORDER BY i, pos"
    )
    try:
        query = await query_service.run_internal_query(
            db,
            workspace=workspace,
            user_id=user.id,
            sql=sql,
            origin="describe",
            active_catalog=cat.slug,
            wait_s=settings.sql_session_wait_timeout_s,
        )
    except GrantDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "grant_denied", "detail": str(exc)},
        ) from exc
    except query_service.ComputeStarting as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "compute_starting", "detail": str(exc)},
            headers={"Retry-After": "5"},
        ) from exc
    except query_service.AgentUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "unavailable", "detail": str(exc)},
        ) from exc
    if query.status == "failed":
        return None
    if query.status != "done":
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"error": "upstream_timeout", "detail": "The agent did not answer in time."},
        )

    out: dict[tuple[str, str], list[MetadataColumnOut]] = {key: [] for key in tables}
    offset = 0
    while True:
        page = await query_service.fetch_result_page(db, query, limit=_PAGE_ROWS, offset=offset)
        for row in page.rows:
            out[tables[int(row["i"])]].append(
                MetadataColumnOut(
                    name=row["column_name"],
                    type=row["column_type"],
                    nullable=row["nullable"] == "YES",
                )
            )
        if page.cursor is None:
            return out
        offset += _PAGE_ROWS
