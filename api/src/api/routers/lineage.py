"""Import lineage produced outside DuckHaven, retire a producer's edges, and
reconstruct the graph from history.

Two ways in, one path through: the generic endpoint takes an already-canonical
edge list, and the per-producer endpoint takes that producer's own artifact and
runs it through an adapter first. Both land in
:func:`api.services.lineage.ingest.upsert_edges`, so identity, deduplication and
reconciliation are decided in one place regardless of who is calling.

Importing is a **write**: asserting that a table was built from something is a
claim about that table, so it needs ``writer`` on the target's catalog wherever
that catalog is attached scoped, on top of workspace ``writer``. Reading it back
is separately redacted, so a scoped principal cannot import a graph naming
objects they cannot see and then read the names out of it.

Backfill sits here too rather than under ``/admin``, because it is scoped to one
workspace's own history and its own catalogs — the same blast radius as purging a
provider, which is why it takes the same workspace ``owner``. The request only
*queues* the work; a background runner walks the history a batch at a time, so
neither a large history nor a slow one can hold a request open or compete with
the queries it is reading about.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.catalog import Catalog
from api.models.lineage_backfill import ACTIVE_STATUSES as ACTIVE_BACKFILL_STATUSES
from api.models.lineage_backfill import LineageBackfill as LineageBackfillModel
from api.models.user import User
from api.schemas.lineage import (
    LineageBackfillIn,
    LineageBackfillOut,
    LineageImportIn,
    LineageImportOut,
    LineageSkippedOut,
)
from api.services import grants as grant_service
from api.services.lineage import backfill as lineage_backfill
from api.services.lineage import ingest as lineage_ingest
from api.services.lineage.ingest import EXECUTION_PROVIDER, CanonicalEdge
from api.services.lineage.keys import AssetRef
from api.services.lineage.providers import get_adapter
from api.services.lineage.resolve import Resolver, Skipped
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)

router = APIRouter()

# A single request's ceiling. Large projects chunk, rather than the server
# holding an unbounded edge list in memory while it resolves every endpoint.
MAX_EDGES_PER_IMPORT = 5000


_RECONCILE_MODES = ("none", "provider_run")


def _validate_reconcile(reconcile: str) -> None:
    """Both import routes accept `reconcile`, so both must reject a bad value.

    Silently ignoring an unrecognised mode is worse here than elsewhere: these
    endpoints are meant to run unattended in CI, where a typo that returns 200
    and quietly stops pruning is invisible until the graph is wrong.
    """
    if reconcile not in _RECONCILE_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"reconcile must be one of: {', '.join(_RECONCILE_MODES)}",
        )


def _require_run_id(reconcile: str, run_id: str | None) -> None:
    """Reject a reconcile the caller cannot possibly satisfy, before any writing.

    Only the generic route needs this: an artifact carries its own run id, and
    one that has none degrades to `reconcile="none"` rather than failing.
    Checked up front because the alternative -- discovering it after the whole
    upsert pass has run -- does the entire write for a request it then rejects.
    """
    if reconcile == "provider_run" and not run_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="reconcile='provider_run' requires run_id",
        )


def _reject_reserved(provider: str) -> None:
    if provider == EXECUTION_PROVIDER:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"'{EXECUTION_PROVIDER}' is reserved for lineage DuckHaven derives from "
                "SQL it ran and cannot be imported. Use a provider name of your own."
            ),
        )


async def _assert_can_write_targets(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    principal_id: uuid.UUID,
    catalogs: list[Catalog],
    edges: list[CanonicalEdge],
) -> None:
    """Require ``writer`` on every internal target the payload writes lineage for."""
    by_id = {c.id: c for c in catalogs}
    checked: set[tuple[uuid.UUID, str, str]] = set()
    for edge in edges:
        target: AssetRef = edge.target
        if target.catalog_id is None:
            continue  # external targets are not DuckHaven objects to guard
        node = (target.catalog_id, target.schema, target.table)
        if node in checked:
            continue
        checked.add(node)
        catalog = by_id.get(target.catalog_id)
        if catalog is None:
            continue
        await grant_service.enforce_leaf(
            db,
            workspace_id,
            catalog,
            principal_id,
            schema=target.schema,
            table=target.table,
            need="writer",
        )


async def _persist(
    db: AsyncSession,
    *,
    provider: str,
    run_id: str | None,
    reconcile: str,
    edges: list[CanonicalEdge],
    skipped: list[Skipped],
    workspace_id: uuid.UUID,
    reconcile_targets: set[str] | None = None,
) -> LineageImportOut:
    result = await lineage_ingest.upsert_edges(
        db,
        edges,
        provider=provider,
        provider_run_id=run_id,
        workspace_id=workspace_id,
    )
    removed = 0
    if reconcile == "provider_run" and run_id:
        removed = await lineage_ingest.reconcile_provider_run(
            db,
            provider=provider,
            provider_run_id=run_id,
            # Defaults to the edges' targets; an adapter that knows the wider
            # set it describes passes that instead, so an asset whose last
            # dependency was removed can still be pruned.
            target_keys=(
                reconcile_targets
                if reconcile_targets is not None
                else {e.target.key for e in edges}
            ),
        )
    await db.commit()
    return LineageImportOut(
        created=result.created,
        updated=result.updated,
        removed=removed,
        skipped=[LineageSkippedOut(ref=s.ref, reason=s.reason) for s in skipped],
    )


async def import_lineage(
    ws: str,
    body: LineageImportIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LineageImportOut:
    """Import a batch of already-canonical edges from any producer."""
    _reject_reserved(body.provider)
    _validate_reconcile(body.reconcile)
    _require_run_id(body.reconcile, body.run_id)
    if len(body.edges) > MAX_EDGES_PER_IMPORT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"At most {MAX_EDGES_PER_IMPORT} edges per request; split the import.",
        )

    workspace = await get_workspace(db, ws)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    resolver = Resolver(catalogs)

    edges: list[CanonicalEdge] = []
    skipped: list[Skipped] = []
    for edge in body.edges:
        # An unresolvable source becomes an external node rather than a dropped
        # root; an unresolvable target is a mistake and is reported.
        source, source_skip = resolver.resolve(
            catalog=edge.source.catalog,
            system=edge.source.system,
            schema=edge.source.schema_name,
            table=edge.source.table,
            allow_external=True,
        )
        target, target_skip = resolver.resolve(
            catalog=edge.target.catalog,
            system=edge.target.system,
            schema=edge.target.schema_name,
            table=edge.target.table,
            allow_external=False,
        )
        if source is None or target is None:
            skipped.extend(s for s in (source_skip, target_skip) if s is not None)
            continue
        edges.append(
            CanonicalEdge(
                source=source,
                target=target,
                operation=edge.operation,
                confidence=edge.confidence,
            )
        )

    await _assert_can_write_targets(
        db,
        workspace_id=workspace.id,
        principal_id=user.id,
        catalogs=catalogs,
        edges=edges,
    )
    return await _persist(
        db,
        provider=body.provider,
        run_id=body.run_id,
        reconcile=body.reconcile,
        edges=edges,
        skipped=skipped,
        workspace_id=workspace.id,
    )


async def import_provider_artifact(
    ws: str,
    provider: str,
    body: dict = Body(...),
    reconcile: str = "provider_run",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LineageImportOut:
    """Import a producer's own artifact, translated by that producer's adapter.

    Defaults to reconciling, because an artifact describes a producer's whole
    view of the world at one moment — the case where pruning what it no longer
    mentions is the correct behaviour.
    """
    _reject_reserved(provider)
    _validate_reconcile(reconcile)
    try:
        adapter = get_adapter(provider)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No lineage adapter for provider '{provider}'",
        ) from None

    workspace = await get_workspace(db, ws)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    catalogs = await resolve_workspace_catalogs(db, workspace.id)

    produced = adapter(body, resolve=Resolver(catalogs))
    edges, skipped = produced.edges, produced.skipped
    if len(edges) > MAX_EDGES_PER_IMPORT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Artifact yields more than {MAX_EDGES_PER_IMPORT} edges; split the project.",
        )

    run_id = None
    if provider == "dbt":
        from api.services.lineage.providers.dbt import run_id as dbt_run_id

        run_id = dbt_run_id(body)

    await _assert_can_write_targets(
        db,
        workspace_id=workspace.id,
        principal_id=user.id,
        catalogs=catalogs,
        edges=edges,
    )
    return await _persist(
        db,
        provider=provider,
        run_id=run_id,
        reconcile=reconcile if run_id else "none",
        edges=edges,
        skipped=skipped,
        workspace_id=workspace.id,
        reconcile_targets=produced.targets,
    )


async def purge_provider_lineage(
    ws: str,
    provider: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LineageImportOut:
    """Remove every edge a retired producer asserted.

    Workspace-addressed for consistency with the import endpoints, but edges are
    catalog-scoped facts, so this removes the provider's edges wherever they are.
    Requires workspace ``owner``: it is a destructive, cross-catalog operation.
    """
    workspace = await get_workspace(db, ws)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    try:
        removed = await lineage_ingest.purge_provider(db, provider=provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await db.commit()
    return LineageImportOut(created=0, updated=0, removed=removed)


def _backfill_out(row: LineageBackfillModel) -> LineageBackfillOut:
    return LineageBackfillOut(
        status=row.status,
        dry_run=row.dry_run,
        since=row.since_at,
        covered_from=row.covered_from,
        queries_scanned=row.queries_scanned,
        queries_with_lineage=row.queries_with_lineage,
        queries_skipped=row.queries_skipped,
        parse_failures=row.parse_failures,
        queries_failed=row.queries_failed,
        edges_created=row.edges_created,
        edges_updated=row.edges_updated,
        error=row.error,
        requested_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


async def start_backfill(
    ws: str,
    body: LineageBackfillIn = Body(default_factory=LineageBackfillIn),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LineageBackfillOut:
    """Queue a reconstruction of this workspace's lineage from its query history.

    Returns immediately with the queued state; poll the same path to watch it.
    Requires workspace ``owner``: it reads every statement the workspace has ever
    run and writes lineage across every catalog it attaches, which is the same
    reach as retiring a provider.
    """
    workspace = await get_workspace(db, ws)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    try:
        row = await lineage_backfill.request_backfill(
            db,
            workspace_id=workspace.id,
            since=body.since,
            dry_run=body.dry_run,
            requested_by=user.id,
        )
    except lineage_backfill.BackfillInProgress as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    return _backfill_out(row)


async def get_backfill(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LineageBackfillOut:
    """How far this workspace's lineage backfill has got, and what it found."""
    workspace = await get_workspace(db, ws)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    row = (
        await db.execute(
            sa.select(LineageBackfillModel).where(LineageBackfillModel.workspace_id == workspace.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No lineage backfill has been requested for this workspace",
        )
    return _backfill_out(row)


async def cancel_backfill(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LineageBackfillOut:
    """Ask a running backfill to stop at the next batch boundary.

    Cooperative rather than immediate: the runner checks between batches, so the
    cursor never ends up claiming history it did not finish reading. Whatever was
    written before the cancel stays — it is real lineage, derived the same way as
    everything else.
    """
    workspace = await get_workspace(db, ws)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    row = (
        await db.execute(
            sa.select(LineageBackfillModel).where(LineageBackfillModel.workspace_id == workspace.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No lineage backfill has been requested for this workspace",
        )
    if row.status in ACTIVE_BACKFILL_STATUSES:
        row.cancel_requested = True
        await db.commit()
    return _backfill_out(row)


router.add_api_route(
    "/workspaces/{ws}/lineage/imports",
    import_lineage,
    methods=["POST"],
    response_model=LineageImportOut,
)
router.add_api_route(
    "/workspaces/{ws}/lineage/imports/{provider}",
    import_provider_artifact,
    methods=["POST"],
    response_model=LineageImportOut,
)
router.add_api_route(
    "/workspaces/{ws}/lineage/imports",
    purge_provider_lineage,
    methods=["DELETE"],
    response_model=LineageImportOut,
)
router.add_api_route(
    "/workspaces/{ws}/lineage/backfill",
    start_backfill,
    methods=["POST"],
    response_model=LineageBackfillOut,
    status_code=status.HTTP_202_ACCEPTED,
)
router.add_api_route(
    "/workspaces/{ws}/lineage/backfill",
    get_backfill,
    methods=["GET"],
    response_model=LineageBackfillOut,
)
router.add_api_route(
    "/workspaces/{ws}/lineage/backfill",
    cancel_backfill,
    methods=["DELETE"],
    response_model=LineageBackfillOut,
)
