"""Schemas + tables endpoints (M3, G-D8-b, G-D9-a).

Endpoints are catalog-scoped and gated by `assert_workspace_member`. Each is
exposed twice: the canonical
``/workspaces/{workspace}/catalogs/{catalog}/schemas/...`` form, and a legacy
``/workspaces/{workspace}/schemas/...`` shim that resolves the workspace's *default*
catalog (backward compatibility for pre-multi-catalog clients). list operations
require `reader`; creates/drops require `writer`. Polaris is the authority — we
never write schema/table state into pg.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.models.workspace import Workspace
from api.schemas.catalog import (
    AllowedColumnType,
    CatalogSchemaCreate,
    CatalogSchemaOut,
    ColumnSpec,
    SnapshotOut,
    TableColumnOut,
    TableCreate,
    TableOut,
)
from api.schemas.lineage import LineageGraphOut
from api.schemas.query import RowsPageOut
from api.services import grants as grant_service
from api.services import query as query_service
from api.services.lineage import graph as lineage_graph
from api.services.lineage import ingest as lineage_ingest
from api.services.polaris import (
    PolarisClient,
    PolarisConflictError,
    PolarisNotFoundError,
    PolarisSnapshot,
    PolarisTable,
)
from api.services.semantic import impact as semantic_impact
from api.services.workspace import (
    assert_workspace_member,
    ensure_polaris_catalog,
    get_default_catalog,
    get_workspace,
    polaris_storage,
    resolve_catalog,
    resolve_workspace_catalogs,
)

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
    snapshot_id: int | None
    snapshot_at: datetime | None
    data_file_count: int | None
    has_deletes: bool | None


async def _load_table_meta(
    db: AsyncSession, catalog_id: uuid.UUID, schema_name: str
) -> dict[str, _TableMeta]:
    """Load TableMetadata for a schema, resolving owner/writer/agent to display names."""
    rows = list(
        (
            await db.execute(
                select(TableMetadata).where(
                    TableMetadata.catalog_id == catalog_id,
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
            snapshot_id=r.snapshot_id,
            snapshot_at=r.snapshot_at,
            data_file_count=r.data_file_count,
            has_deletes=r.has_deletes,
        )
        for r in rows
    }


async def _delete_table_meta(
    db: AsyncSession, catalog_id: uuid.UUID, schema_name: str, table_name: str
) -> None:
    """Remove the TableMetadata sidecar row for a dropped table, if present."""
    existing = (
        await db.execute(
            select(TableMetadata).where(
                TableMetadata.catalog_id == catalog_id,
                TableMetadata.schema_name == schema_name,
                TableMetadata.table_name == table_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)


# Map the small set of allowed scalar types to Iceberg primitive type strings.
_TYPE_TO_ICEBERG: dict[AllowedColumnType, str] = {
    "INTEGER": "int",
    "BIGINT": "long",
    "DOUBLE": "double",
    "VARCHAR": "string",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "DECIMAL": "decimal(38,9)",
}


def _column_for_iceberg(spec: ColumnSpec, field_id: int) -> dict[str, object]:
    """Build an Iceberg schema field. Field ids are 1-based and unique."""
    return {
        "id": field_id,
        "name": spec.name,
        "required": not spec.nullable,
        "type": _TYPE_TO_ICEBERG[spec.type],
    }


def _table_to_out(
    table: PolarisTable,
    catalog: Catalog,
    workspace_id: uuid.UUID,
    meta: _TableMeta | None = None,
) -> TableOut:
    props = table.properties or {}
    return TableOut(
        name=table.name,
        schema_name=table.schema_name,
        catalog=catalog.slug,
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
        # Every Iceberg REST table is catalog-managed by definition.
        catalog_commits=True,
        row_count=meta.row_count if meta else None,
        row_count_estimate=(
            _snapshot_metric(table.current_snapshot_summary, "total-records")
            if table.current_snapshot_summary
            else None
        ),
        size_bytes=meta.size_bytes if meta else None,
        owner=meta.owner if meta else None,
        last_write_at=meta.last_write_at if meta else None,
        last_write_by=meta.last_write_by if meta else None,
        last_write_agent=meta.last_write_agent if meta else None,
        format_version=table.format_version,
        snapshot_id=(str(meta.snapshot_id) if meta and meta.snapshot_id is not None else None),
        snapshot_at=meta.snapshot_at if meta else None,
        data_file_count=meta.data_file_count if meta else None,
        has_deletes=meta.has_deletes if meta else None,
    )


def _snapshot_metric(summary: dict[str, str], key: str) -> int | None:
    """Parse a numeric Iceberg snapshot-summary metric; None if absent/garbage."""
    raw = summary.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except TypeError, ValueError:
        return None


def _snapshot_to_out(snap: PolarisSnapshot) -> SnapshotOut:
    return SnapshotOut(
        snapshot_id=str(snap.snapshot_id),
        parent_snapshot_id=(
            str(snap.parent_snapshot_id) if snap.parent_snapshot_id is not None else None
        ),
        committed_at=datetime.fromtimestamp(snap.timestamp_ms / 1000, tz=UTC),
        operation=snap.operation,
        is_current=snap.is_current,
        schema_id=snap.schema_id,
        added_records=_snapshot_metric(snap.summary, "added-records"),
        deleted_records=_snapshot_metric(snap.summary, "deleted-records"),
        total_records=_snapshot_metric(snap.summary, "total-records"),
        added_data_files=_snapshot_metric(snap.summary, "added-data-files"),
        total_data_files=_snapshot_metric(snap.summary, "total-data-files"),
    )


@dataclass
class _Target:
    """The (workspace, catalog) a schemas/tables request resolved to."""

    workspace: Workspace
    catalog: Catalog


def target_catalog(
    min_role: str,
) -> Callable[..., Coroutine[Any, Any, _Target]]:
    """Dependency factory resolving the request's target catalog.

    On the canonical route ``catalog`` is a path param; on the legacy shim it is
    absent and the workspace's default catalog is used. Membership is enforced at
    ``min_role``.
    """

    async def _dep(
        ws: Annotated[str, Path(alias="workspace")],
        catalog: str | None = None,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> _Target:
        workspace = await get_workspace(db, ws)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        await assert_workspace_member(db, workspace.id, user.id, min_role=min_role)
        if catalog is None:
            resolved = await get_default_catalog(db, workspace.id)
            if resolved is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace has no catalogs attached.",
                )
        else:
            resolved = await resolve_catalog(db, workspace.id, catalog)
        return _Target(workspace=workspace, catalog=resolved)

    return _dep


async def _ensure_catalog(db: AsyncSession, polaris: PolarisClient, catalog: Catalog) -> None:
    backend = catalog.storage_backend
    if backend is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Catalog points to a missing storage backend",
        )
    storage_type, base_location, extra_storage = polaris_storage(
        backend.kind, backend.root_uri, backend.config
    )
    await ensure_polaris_catalog(
        polaris,
        catalog.polaris_name,
        storage_type=storage_type,
        base_location=base_location,
        extra_storage=extra_storage,
    )


# --- Handlers (registered on both the catalog-scoped and legacy routers) ---


async def list_schemas(
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> list[CatalogSchemaOut]:
    """The Iceberg namespaces in the catalog, as the caller is allowed to see them.

    When the catalog is in restricted access mode, namespaces the caller holds no
    grant on are filtered out rather than refused -- a listing should not leak the
    existence of what it hides."""
    cat = target.catalog
    await _ensure_catalog(db, polaris, cat)
    schemas = await polaris.list_schemas(cat.polaris_name)
    if await grant_service.is_scoped(db, target.workspace.id, cat):
        visible = await grant_service.visible_schemas(
            db, target.workspace.id, cat, user.id, [s.name for s in schemas]
        )
        schemas = [s for s in schemas if s.name in visible]
    return [
        CatalogSchemaOut(
            name=s.name,
            catalog=cat.slug,
            catalog_name=s.catalog_name,
            workspace_id=str(target.workspace.id),
        )
        for s in schemas
    ]


async def create_schema(
    body: CatalogSchemaCreate,
    target: _Target = Depends(target_catalog("writer")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> CatalogSchemaOut:
    """Create an Iceberg namespace in the catalog. Requires `writer`.

    409 if the namespace already exists."""
    cat = target.catalog
    await grant_service.enforce_leaf(
        db, target.workspace.id, cat, user.id, schema=body.name, table=None, need="writer"
    )
    await _ensure_catalog(db, polaris, cat)
    try:
        sc = await polaris.create_schema(cat.polaris_name, body.name)
    except PolarisConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CatalogSchemaOut(
        name=sc.name,
        catalog=cat.slug,
        catalog_name=sc.catalog_name,
        workspace_id=str(target.workspace.id),
    )


async def refresh_table_stats(
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> dict[str, int]:
    """Probe row counts for tables in this catalog that have none yet."""
    workspace, cat = target.workspace, target.catalog

    missing: list[tuple[str, str]] = []
    for s in await polaris.list_schemas(cat.polaris_name):
        meta = await _load_table_meta(db, cat.id, s.name)
        for t in await polaris.list_tables(cat.polaris_name, s.name):
            m = meta.get(t.name)
            if m is None or m.row_count is None:
                missing.append((s.name, t.name))

    # In scoped mode a row-count probe reads data, so only probe tables the
    # principal has at least `reader` on (a metadata-tier table stays hidden).
    if missing and await grant_service.is_scoped(db, workspace.id, cat):
        allowed = []
        for schema_name, table_name in missing:
            tier = await grant_service.node_tier(
                db, workspace.id, cat, user.id, schema_name, table_name
            )
            if grant_service.tier_rank(tier) >= grant_service.TIER_SCALE["reader"]:
                allowed.append((schema_name, table_name))
        missing = allowed

    if not missing:
        return {"probed": 0}

    agent = await query_service.pick_agent_for(db, workspace, principal_id=user.id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No compatible agent is connected to refresh stats.",
        )

    probed = 0
    for schema_name, table_name in missing:
        query = await query_service.run_sync_query(
            db,
            workspace=workspace,
            agent=agent,
            user_id=user.id,
            sql="SELECT 1",
            origin="sample",
            active_catalog=cat.slug,
            stats_for={"catalog": cat.slug, "schema": schema_name, "table": table_name},
        )
        if query.status == "done":
            probed += 1

    return {"probed": probed}


async def drop_schema(
    schema: str,
    cascade: bool = False,
    target: _Target = Depends(target_catalog("writer")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> None:
    """Drop a schema. Fails if it still holds tables unless `cascade=true`."""
    cat = target.catalog
    await grant_service.enforce_leaf(
        db, target.workspace.id, cat, user.id, schema=schema, table=None, need="writer"
    )
    try:
        tables = await polaris.list_tables(cat.polaris_name, schema)
    except PolarisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    if tables and not cascade:
        names = ", ".join(t.name for t in tables)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Schema '{schema}' is not empty (tables: {names}). "
                "Pass cascade=true to drop them too."
            ),
        )
    for t in tables:
        await polaris.delete_table(cat.polaris_name, schema, t.name, purge=True)
        await _delete_table_meta(db, cat.id, schema, t.name)
    try:
        await polaris.delete_schema(cat.polaris_name, schema)
    except PolarisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    # Drop dangling grants for the schema and every table that was under it.
    await grant_service.delete_schema_grants(db, cat.id, schema)
    await lineage_ingest.delete_schema_lineage(db, cat.id, schema)
    await semantic_impact.mark_bindings_broken(
        db,
        catalog_id=cat.id,
        schema_name=schema,
        detail=f"Schema '{schema}' was dropped.",
    )
    await db.commit()


async def list_tables(
    schema: str,
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> list[TableOut]:
    """The tables in one namespace, filtered by grant the same way schemas are.

    Each row carries the control-plane metadata the browser needs -- owner, last
    writer, row count -- so rendering a table list costs one request, not one per
    table."""
    cat = target.catalog
    tables = await polaris.list_tables(cat.polaris_name, schema)
    if await grant_service.is_scoped(db, target.workspace.id, cat):
        visible = await grant_service.visible_tables(
            db, target.workspace.id, cat, user.id, schema, [t.name for t in tables]
        )
        tables = [t for t in tables if t.name in visible]
    meta = await _load_table_meta(db, cat.id, schema)
    return [_table_to_out(t, cat, target.workspace.id, meta.get(t.name)) for t in tables]


async def get_table(
    schema: str,
    table: str,
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> TableOut:
    """One table's schema and control-plane metadata.

    Needs only the `metadata` tier: seeing that a table exists and what its
    columns are is weaker than reading its rows."""
    cat = target.catalog
    await grant_service.enforce_leaf(
        db, target.workspace.id, cat, user.id, schema=schema, table=table, need="metadata"
    )
    try:
        t = await polaris.get_table(cat.polaris_name, schema, table)
    except PolarisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    # The one place DuckHaven reliably holds a table's Iceberg identity without
    # asking for it, so it is where a rename gets noticed and the lineage that
    # would otherwise be orphaned is moved across. A no-op — and no write — once
    # the identity is already on record, which is every call but the first.
    await _reconcile_identity(db, cat.id, schema, table, t.table_id)
    meta = await _load_table_meta(db, cat.id, schema)
    return _table_to_out(t, cat, target.workspace.id, meta.get(table))


async def _reconcile_identity(
    db: AsyncSession, catalog_id: uuid.UUID, schema: str, table: str, table_uuid: str | None
) -> None:
    """Record a table's Iceberg identity, repairing lineage if it has moved.

    Lineage is metadata: failing to reconcile it must never turn a table anyone
    can otherwise read into an error. A failure here is logged and the read
    continues, and the next look at the table tries again.
    """
    from api.services.lineage.identity import reconcile_table_identity

    async def _follow_rename(old_schema: str, old_table: str) -> None:
        # Semantic bindings follow the table for the same reason lineage does:
        # the definition still describes this data, it just lives under a new
        # name now.
        await semantic_impact.rekey_bindings(
            db,
            catalog_id=catalog_id,
            old_schema=old_schema,
            old_table=old_table,
            new_schema=schema,
            new_table=table,
        )

    try:
        outcome = await reconcile_table_identity(
            db,
            catalog_id=catalog_id,
            schema=schema,
            table=table,
            table_uuid=table_uuid,
            on_rename=_follow_rename,
        )
        if outcome == "recreated":
            # Same name, different table. The columns an expression names may or
            # may not still be there, and assuming they are is how a definition
            # starts quietly reading something else.
            await semantic_impact.mark_bindings_unchecked(
                db, catalog_id=catalog_id, schema_name=schema, table_name=table
            )
        if outcome not in (None, "unchanged"):
            await db.commit()
    except Exception:
        logger.exception("Lineage identity reconcile failed for %s.%s", schema, table)
        await db.rollback()


async def list_snapshots(
    schema: str,
    table: str,
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> list[SnapshotOut]:
    """Iceberg snapshot history for a table, newest first (live from Polaris)."""
    cat = target.catalog
    await grant_service.enforce_leaf(
        db, target.workspace.id, cat, user.id, schema=schema, table=table, need="metadata"
    )
    try:
        snapshots = await polaris.list_snapshots(cat.polaris_name, schema, table)
    except PolarisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    return [_snapshot_to_out(s) for s in snapshots]


async def get_table_lineage(
    schema: str,
    table: str,
    direction: str = "both",
    depth: int = 2,
    provider: Annotated[list[str] | None, Query()] = None,
    columns_for: Annotated[list[str] | None, Query()] = None,
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LineageGraphOut:
    """The bounded lineage graph around a table.

    Reads only from the control plane's own store — no Polaris call and no agent
    round trip — so it stays cheap enough to open a tab on. Nodes the caller
    cannot see are redacted rather than dropped, and nodes outside the
    workspace's attached catalogs are dropped rather than redacted; see
    ``services/lineage/redact.py`` for why those two differ.

    ``columns_for`` is a repeatable node key naming which nodes should come back
    with their column-level detail attached, and defaults to none of them. The
    table graph is bounded by node count, but its column detail is bounded by how
    wide those tables are — so a caller that wants one node's columns asks for
    that node, and the cost of opening the tab does not depend on the shape of
    tables nobody is looking at.
    """
    if direction not in ("upstream", "downstream", "both"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="direction must be one of: upstream, downstream, both",
        )
    cat = target.catalog
    await grant_service.enforce_leaf(
        db, target.workspace.id, cat, user.id, schema=schema, table=table, need="metadata"
    )
    catalogs = await resolve_workspace_catalogs(db, target.workspace.id)
    return await lineage_graph.table_lineage(
        db,
        workspace_id=target.workspace.id,
        principal_id=user.id,
        catalogs=catalogs,
        catalog=cat,
        schema=schema,
        table=table,
        direction=direction,
        depth=depth,
        providers=provider,
        columns_for=set(columns_for) if columns_for else None,
    )


async def create_table(
    schema: str,
    body: TableCreate,
    target: _Target = Depends(target_catalog("writer")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> TableOut:
    """Create an Iceberg table from a column spec. Requires `writer`.

    409 if the table already exists."""
    cat = target.catalog
    await grant_service.enforce_leaf(
        db, target.workspace.id, cat, user.id, schema=schema, table=body.name, need="writer"
    )
    columns = [_column_for_iceberg(spec, idx + 1) for idx, spec in enumerate(body.columns)]

    await _ensure_catalog(db, polaris, cat)
    try:
        t = await polaris.create_table(
            catalog=cat.polaris_name,
            schema=schema,
            name=body.name,
            columns=columns,
        )
    except PolarisConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.add(
        TableMetadata(
            catalog_id=cat.id,
            schema_name=schema,
            table_name=body.name,
            # Recorded at birth, so this table's identity is on file before
            # anything can rename it out from under its lineage.
            table_uuid=t.table_id,
            owner_id=user.id,
            row_count=0,
            size_bytes=0,
            last_write_at=datetime.now(tz=UTC),
            last_write_by_id=user.id,
        )
    )
    await db.commit()
    meta = await _load_table_meta(db, cat.id, schema)
    return _table_to_out(t, cat, target.workspace.id, meta.get(body.name))


async def drop_table(
    schema: str,
    table: str,
    target: _Target = Depends(target_catalog("writer")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> None:
    """Drop a table and purge its data. Requires `writer`.

    Irreversible: the Iceberg metadata and the underlying files both go. The
    table's grants and lineage go with it; its query history does not, because
    the history is an audit record of what was run, not of what still exists."""
    cat = target.catalog
    await grant_service.enforce_leaf(
        db, target.workspace.id, cat, user.id, schema=schema, table=table, need="writer"
    )
    try:
        await polaris.delete_table(cat.polaris_name, schema, table, purge=True)
    except PolarisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    await _delete_table_meta(db, cat.id, schema, table)
    await grant_service.delete_table_grants(db, cat.id, schema, table)
    await lineage_ingest.delete_table_lineage(db, cat.id, schema, table)
    # Deliberately *not* deleted, unlike the grants and lineage above. Those
    # describe the table and stop meaning anything once it is gone; a semantic
    # definition describes the business and outlives the table it happened to be
    # bound to. Deleting it would discard somebody's work and quietly return the
    # assistant to inventing its own revenue calculation, so it is marked broken
    # instead: visible, withheld from the assistant, and repairable by rebinding.
    await semantic_impact.mark_bindings_broken(
        db,
        catalog_id=cat.id,
        schema_name=schema,
        table_name=table,
        detail=f"{schema}.{table} was dropped.",
    )
    await db.commit()


# Rows previewed in the table-detail view.
_SAMPLE_LIMIT = 20


async def sample_table(
    schema: str,
    table: str,
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> RowsPageOut:
    """A page of rows from the table, for previewing it without writing SQL.

    Runs on an agent like any other query, so it needs one connected and returns
    503 when none is available."""
    workspace, cat = target.workspace, target.catalog
    await grant_service.enforce_leaf(
        db, workspace.id, cat, user.id, schema=schema, table=table, need="reader"
    )
    try:
        await polaris.get_table(cat.polaris_name, schema, table)
    except PolarisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc

    agent = await query_service.pick_agent_for(db, workspace, principal_id=user.id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No compatible agent is connected to preview this table.",
        )

    sql = f'SELECT * FROM "{cat.slug}"."{schema}"."{table}" LIMIT {_SAMPLE_LIMIT}'
    query = await query_service.run_sync_query(
        db,
        workspace=workspace,
        agent=agent,
        user_id=user.id,
        sql=sql,
        origin="sample",
        active_catalog=cat.slug,
        stats_for={"catalog": cat.slug, "schema": schema, "table": table},
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
    return RowsPageOut(
        rows=rows,
        columns=columns,
        cursor=None,
        total=query.row_count or len(rows),
        column_schema=query.result_schema,
    )


async def recount_table(
    schema: str,
    table: str,
    target: _Target = Depends(target_catalog("reader")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> dict[str, int | None]:
    """Force a fresh row-count probe for a single table."""
    workspace, cat = target.workspace, target.catalog
    await grant_service.enforce_leaf(
        db, workspace.id, cat, user.id, schema=schema, table=table, need="reader"
    )
    try:
        await polaris.get_table(cat.polaris_name, schema, table)
    except PolarisNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc

    agent = await query_service.pick_agent_for(db, workspace, principal_id=user.id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No compatible agent is connected to recount this table.",
        )

    query = await query_service.run_sync_query(
        db,
        workspace=workspace,
        agent=agent,
        user_id=user.id,
        sql="SELECT 1",
        origin="sample",
        active_catalog=cat.slug,
        stats_for={"catalog": cat.slug, "schema": schema, "table": table},
    )
    if query.status != "done":
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Recount did not complete in time.",
        )

    meta = (await _load_table_meta(db, cat.id, schema)).get(table)
    return {"row_count": meta.row_count if meta else None}


# --- Route registration: canonical (catalog-scoped) + legacy (default catalog) ---

router = APIRouter()

_CANON = "/workspaces/{workspace}/catalogs/{catalog}/schemas"

# (suffix, handler, methods, extra kwargs)
_ROUTES: list[tuple[str, Callable[..., Any], list[str], dict[str, Any]]] = [
    ("", list_schemas, ["GET"], {"response_model": list[CatalogSchemaOut]}),
    ("", create_schema, ["POST"], {"response_model": CatalogSchemaOut, "status_code": 201}),
    ("/{schema}", drop_schema, ["DELETE"], {"status_code": 204}),
    ("/{schema}/tables", list_tables, ["GET"], {"response_model": list[TableOut]}),
    ("/{schema}/tables", create_table, ["POST"], {"response_model": TableOut, "status_code": 201}),
    ("/{schema}/tables/{table}", get_table, ["GET"], {"response_model": TableOut}),
    ("/{schema}/tables/{table}", drop_table, ["DELETE"], {"status_code": 204}),
    (
        "/{schema}/tables/{table}/snapshots",
        list_snapshots,
        ["GET"],
        {"response_model": list[SnapshotOut]},
    ),
    (
        "/{schema}/tables/{table}/lineage",
        get_table_lineage,
        ["GET"],
        {"response_model": LineageGraphOut},
    ),
    ("/{schema}/tables/{table}/sample", sample_table, ["GET"], {"response_model": RowsPageOut}),
    ("/{schema}/tables/{table}/recount", recount_table, ["POST"], {}),
]

for _suffix, _fn, _methods, _kw in _ROUTES:
    router.add_api_route(_CANON + _suffix, _fn, methods=_methods, **_kw)

# Refreshing stats walks every schema in the catalog, so it is a catalog-level
# operation. Under `/schemas/` it also sat in the `{schema}` slot, where a
# namespace actually named `refresh-stats` would have been unreachable.
router.add_api_route(
    "/workspaces/{workspace}/catalogs/{catalog}/refresh-stats",
    refresh_table_stats,
    methods=["POST"],
)
