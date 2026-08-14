"""Import lineage produced outside DuckHaven, and retire a producer's edges.

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
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.catalog import Catalog
from api.models.user import User
from api.schemas.lineage import (
    LineageImportIn,
    LineageImportOut,
    LineageSkippedOut,
)
from api.services import grants as grant_service
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
) -> LineageImportOut:
    result = await lineage_ingest.upsert_edges(
        db,
        edges,
        provider=provider,
        provider_run_id=run_id,
        workspace_id=workspace_id,
    )
    removed = 0
    if reconcile == "provider_run":
        if not run_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="reconcile='provider_run' requires run_id",
            )
        removed = await lineage_ingest.reconcile_provider_run(
            db,
            provider=provider,
            provider_run_id=run_id,
            target_keys={e.target.key for e in edges},
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
    if body.reconcile not in ("none", "provider_run"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="reconcile must be one of: none, provider_run",
        )
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

    edges, skipped = adapter(body, resolve=Resolver(catalogs))
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
