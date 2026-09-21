"""Drive one catalog export from pending to a terminal state.

Two phases. ``pending`` creates the Iceberg target and attaches it to the same
workspace; ``copying`` dispatches the COPY and then watches the Query row it
created.

Attaching the target is what makes this work with **no agent changes at all**:
`dispatch_query` already attaches every catalog bound to the workspace, so both
the DuckLake source and the Iceberg target land on one connection, and
`pick_agent_for` already requires an agent that can serve both kinds.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import KIND_ICEBERG_POLARIS, Catalog
from api.models.catalog_export import CatalogExport
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace
from api.services.export import (
    STATUS_COMPLETED,
    STATUS_COPYING,
    STATUS_FAILED,
    STATUS_PENDING,
)
from api.services.polaris import PolarisClient

logger = logging.getLogger(__name__)

# Generous: a whole-catalog deep copy is unbounded, and the alternative to
# waiting is a half-copied target.
EXPORT_TIMEOUT_S = 12 * 3600


def copy_statement(source_slug: str, target_slug: str) -> str:
    """The one statement that does the whole catalog.

    Slugs are identifiers here, not values, so they are quoted as such. They are
    already constrained to ``^[a-z][a-z0-9_]*$`` by `validate_catalog_slug`, and
    quoting them anyway costs nothing and survives that constraint loosening.
    """
    return f'COPY FROM DATABASE "{source_slug}" TO "{target_slug}"'


async def process_export(db: AsyncSession, polaris: PolarisClient, export: CatalogExport) -> None:
    """Advance one export, or fail it with something an operator can act on."""
    try:
        if export.cancel_requested and export.status == STATUS_PENDING:
            await _finish(db, export, STATUS_FAILED, "Cancelled before it started.")
            return
        if export.status == STATUS_PENDING:
            await _provision(db, polaris, export)
        if export.status == STATUS_COPYING:
            await _advance(db, export)
    except Exception as exc:  # noqa: BLE001 - any failure must leave a readable state
        logger.exception("Catalog export %s failed", export.id)
        await db.rollback()
        refreshed = await db.get(CatalogExport, export.id)
        if refreshed is not None and refreshed.status not in (STATUS_COMPLETED, STATUS_FAILED):
            await _finish(db, refreshed, STATUS_FAILED, " ".join(str(exc).split())[:1000])


async def _provision(db: AsyncSession, polaris: PolarisClient, export: CatalogExport) -> None:
    """Create the Iceberg target and attach it beside the source."""
    from api.services import catalog as catalog_service

    source = await db.get(Catalog, export.source_catalog_id)
    backend = await db.get(StorageBackend, export.target_storage_backend_id)
    workspace = await db.get(Workspace, export.workspace_id)
    user = await db.get(User, export.created_by)
    assert source and backend and workspace and user

    export.started_at = datetime.now(tz=UTC)

    if export.target_catalog_id is None:
        target = await catalog_service.create_catalog(
            db,
            polaris,
            name=export.target_name,
            backend=backend,
            created_by=user.id,
            kind=KIND_ICEBERG_POLARIS,
        )
        await catalog_service.attach_catalog(
            db, workspace=workspace, catalog=target, attached_by=user.id
        )
        export.target_catalog_id = target.id
        await db.commit()

    # Namespaces first: COPY FROM DATABASE writes into schemas, and Polaris does
    # not create them implicitly. Called on the Polaris client directly rather
    # than through the seam -- the target is Iceberg by construction, and the
    # seam's create_schema wants a WriteContext that means nothing here.
    from api.services.catalog_backends import backend_for
    from api.services.polaris import PolarisConflictError

    target = await db.get(Catalog, export.target_catalog_id)
    assert target
    source_backend = backend_for(source, polaris=polaris)
    total = 0
    for schema in await source_backend.list_schemas(source):
        try:
            await polaris.create_schema(target.polaris_name, schema.name)
        except PolarisConflictError:
            pass  # A resumed export; the namespace is already there.
        total += len(await source_backend.list_tables(source, schema.name))

    export.tables_total = total
    export.status = STATUS_COPYING
    await db.commit()


async def _advance(db: AsyncSession, export: CatalogExport) -> None:
    """Dispatch the copy, or settle it once the query comes back."""
    if export.query_id is None:
        await _dispatch(db, export)
        return

    query = await db.get(Query, export.query_id)
    if query is None:
        await _finish(db, export, STATUS_FAILED, "The export query record is gone.")
        return
    if query.status == "done":
        export.tables_done = export.tables_total
        await _finish(db, export, STATUS_COMPLETED, None)
    elif query.status in ("failed", "cancelled"):
        # Deliberately not dropping the target: it may be partially populated,
        # and silently deleting data a user can already see in the UI is worse
        # than leaving something they can inspect and remove.
        target = (
            await db.get(Catalog, export.target_catalog_id) if export.target_catalog_id else None
        )
        slug = target.slug if target else export.target_name
        await _finish(
            db,
            export,
            STATUS_FAILED,
            f"{query.error or query.status}. The target catalog '{slug}' exists and may be "
            "partially populated; drop it before retrying.",
        )
    # Otherwise still running; the next tick looks again.


async def _dispatch(db: AsyncSession, export: CatalogExport) -> None:
    from api.services import query as query_service

    source = await db.get(Catalog, export.source_catalog_id)
    target = await db.get(Catalog, export.target_catalog_id) if export.target_catalog_id else None
    workspace = await db.get(Workspace, export.workspace_id)
    assert source and target and workspace

    agent = await query_service.pick_agent_for(db, workspace, principal_id=export.created_by)
    if agent is None:
        await _finish(
            db, export, STATUS_FAILED, "No agent able to serve both catalog kinds is connected."
        )
        return

    sql = copy_statement(source.slug, target.slug)
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=export.created_by,
        sql=sql,
        status="queued",
        origin="export",
    )
    db.add(query)
    await db.flush()
    export.query_id = query.id
    await db.commit()

    await query_service.dispatch_query(
        db,
        query,
        timeout_s=float(EXPORT_TIMEOUT_S),
        active_catalog=source.slug,
        principal_id=export.created_by,
    )


async def _finish(db: AsyncSession, export: CatalogExport, status: str, error: str | None) -> None:
    export.status = status
    export.error = error
    export.finished_at = datetime.now(tz=UTC)
    await db.commit()
