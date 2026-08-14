"""Persist lineage edges, whatever produced them.

The single write path into :class:`~api.models.lineage.LineageEdge`. Producers —
the native extractor and every importer — translate into
:class:`CanonicalEdge` and hand it here; nothing else touches the table, so
identity, deduplication and reconciliation are decided in exactly one place.

``record_execution_lineage`` is called from the agent frame handler when a query
completes, mirroring how
:func:`~api.services.maintenance.ingest.record_health_sample` persists a health
probe from the same hook.

Reconciliation deserves care: it is the only operation here that deletes. It is
always scoped to a single ``provider``, so re-importing a dbt project can never
remove execution-derived edges, and it is further scoped to the targets named in
the payload, so a partial run (``dbt run --select one_model``) does not delete
lineage for the models it did not build.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.lineage import LineageEdge
from api.models.query import Query
from api.services.lineage.extract import LineageParseError, edges_from_sql
from api.services.lineage.keys import AssetRef

logger = logging.getLogger(__name__)

# Reserved: lineage DuckHaven derived itself. The import API rejects it so a
# client cannot forge execution-derived provenance.
EXECUTION_PROVIDER = "execution"

# DuckHaven's own synthetic queries — the row preview behind a table detail view
# and the metadata reads behind editor autocomplete. They are pure reads, so they
# would establish nothing anyway; skipping them keeps the parse off the path that
# runs every time somebody clicks a table in the catalog explorer. They are
# already excluded from query history for the same reason.
_INTERNAL_ORIGINS = frozenset({"sample", "metadata"})


@dataclass(frozen=True)
class CanonicalEdge:
    """One relationship in the form the store accepts, whatever produced it."""

    source: AssetRef
    target: AssetRef
    operation: str | None = None
    confidence: str = "exact"


@dataclass
class IngestResult:
    """What an ingest run changed, for the API response and for tests."""

    created: int = 0
    updated: int = 0
    removed: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)


async def upsert_edges(
    db: AsyncSession,
    edges: list[CanonicalEdge],
    *,
    provider: str,
    provider_run_id: str | None = None,
    workspace_id: uuid.UUID | None = None,
    last_query_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Insert or refresh each edge, returning what changed.

    Idempotent: re-asserting a relationship bumps ``last_seen_at`` and
    ``observation_count`` rather than duplicating the row. Two providers naming
    the same pair keep two rows, because ``provider`` is part of the identity.
    """
    result = IngestResult()
    if not edges:
        return result
    stamp = now or datetime.now(tz=UTC)

    for edge in edges:
        source_key = edge.source.key
        target_key = edge.target.key
        existing = (
            await db.execute(
                sa.select(LineageEdge).where(
                    LineageEdge.provider == provider,
                    LineageEdge.source_key == source_key,
                    LineageEdge.target_key == target_key,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.last_seen_at = stamp
            existing.observation_count = (existing.observation_count or 0) + 1
            existing.operation = edge.operation or existing.operation
            existing.confidence = edge.confidence
            if provider_run_id is not None:
                existing.provider_run_id = provider_run_id
            if last_query_id is not None:
                existing.last_query_id = last_query_id
            if workspace_id is not None:
                existing.workspace_id = workspace_id
            result.updated += 1
            continue

        db.add(
            LineageEdge(
                source_key=source_key,
                source_catalog_id=edge.source.catalog_id,
                source_system=edge.source.system,
                source_schema=edge.source.schema,
                source_table=edge.source.table,
                target_key=target_key,
                target_catalog_id=edge.target.catalog_id,
                target_system=edge.target.system,
                target_schema=edge.target.schema,
                target_table=edge.target.table,
                provider=provider,
                provider_run_id=provider_run_id,
                workspace_id=workspace_id,
                operation=edge.operation,
                confidence=edge.confidence,
                last_query_id=last_query_id,
                first_seen_at=stamp,
                last_seen_at=stamp,
                observation_count=1,
            )
        )
        result.created += 1
    await db.flush()
    return result


async def reconcile_provider_run(
    db: AsyncSession,
    *,
    provider: str,
    provider_run_id: str,
    target_keys: set[str],
) -> int:
    """Drop this provider's edges into ``target_keys`` that the run did not reassert.

    Scoping to the targets present in the payload is what makes a partial import
    safe. A full run names every target and so prunes everything stale; a
    selective run names a handful and leaves the rest of the graph untouched.
    Scoping to ``provider`` is what stops an import from ever deleting lineage
    DuckHaven observed itself.
    """
    if not target_keys:
        return 0
    deleted = await db.execute(
        sa.delete(LineageEdge).where(
            LineageEdge.provider == provider,
            LineageEdge.target_key.in_(target_keys),
            sa.or_(
                LineageEdge.provider_run_id.is_(None),
                LineageEdge.provider_run_id != provider_run_id,
            ),
        )
    )
    return deleted.rowcount or 0


async def purge_provider(db: AsyncSession, *, provider: str) -> int:
    """Remove every edge a provider ever asserted.

    For retiring a producer wholesale — the graph should not keep claiming
    relationships that nothing will ever refresh again.
    """
    if provider == EXECUTION_PROVIDER:
        raise ValueError("Execution-derived lineage cannot be purged by provider")
    deleted = await db.execute(sa.delete(LineageEdge).where(LineageEdge.provider == provider))
    return deleted.rowcount or 0


async def delete_table_lineage(
    db: AsyncSession, catalog_id: uuid.UUID, schema: str, table: str
) -> None:
    """Remove every edge touching a dropped table.

    Deliberately symmetrical with ``_delete_table_meta`` and
    ``grants.delete_table_grants``, which the same drop path already calls: one
    consistent rule — the table is gone, so everything keyed to its name goes with
    it — beats a bespoke tombstone the rest of the codebase knows nothing about.
    Re-creating the table and re-running the transformation restores the edges.
    """
    await db.execute(
        sa.delete(LineageEdge).where(
            sa.or_(
                sa.and_(
                    LineageEdge.source_catalog_id == catalog_id,
                    LineageEdge.source_schema == schema,
                    LineageEdge.source_table == table,
                ),
                sa.and_(
                    LineageEdge.target_catalog_id == catalog_id,
                    LineageEdge.target_schema == schema,
                    LineageEdge.target_table == table,
                ),
            )
        )
    )


async def delete_schema_lineage(db: AsyncSession, catalog_id: uuid.UUID, schema: str) -> None:
    """Remove every edge touching any table in a dropped schema."""
    await db.execute(
        sa.delete(LineageEdge).where(
            sa.or_(
                sa.and_(
                    LineageEdge.source_catalog_id == catalog_id,
                    LineageEdge.source_schema == schema,
                ),
                sa.and_(
                    LineageEdge.target_catalog_id == catalog_id,
                    LineageEdge.target_schema == schema,
                ),
            )
        )
    )


async def _active_catalog(db: AsyncSession, query: Query, catalogs: list) -> str | None:
    """The catalog unqualified names in this query resolved against.

    ``queries.active_catalog`` records only what the *caller* asked for, and is
    NULL whenever the request omitted it — every scheduled run, and any client
    that does not send one. The agent still resolved those names against
    something, so lineage has to resolve them the same way or it silently
    records nothing for the most ordinary statement there is
    (``CREATE TABLE b AS SELECT * FROM a`` with no catalog prefix).

    Deliberately the same fallback ``dispatch_query`` applies before it builds
    the agent payload — workspace default, then the first attached catalog — so
    a name means the same thing to the graph as it did to the engine.
    """
    if query.active_catalog:
        return query.active_catalog
    from api.services.workspace import get_default_catalog

    default = await get_default_catalog(db, query.workspace_id)
    if default is not None:
        return default.slug
    return catalogs[0].slug if catalogs else None


async def record_execution_lineage(db: AsyncSession, query: Query) -> IngestResult:
    """Derive and persist lineage for a query that just completed successfully.

    Called from the agent frame handler. Never raises for a reason the caller
    could not act on: a statement that cannot be parsed, or that establishes no
    relationship, simply records nothing.
    """
    from api.services.workspace import resolve_workspace_catalogs

    result = IngestResult()
    if not query.sql or query.origin in _INTERNAL_ORIGINS:
        return result

    catalogs = await resolve_workspace_catalogs(db, query.workspace_id)
    catalog_ids = {c.slug: c.id for c in catalogs}
    if not catalog_ids:
        return result

    active_catalog = await _active_catalog(db, query, catalogs)
    try:
        extracted = edges_from_sql(
            query.sql, active_catalog=active_catalog, catalog_ids=catalog_ids
        )
    except LineageParseError as exc:
        from api.metrics import record_lineage_extract_failure

        record_lineage_extract_failure()
        logger.debug("Lineage extraction skipped for query %s: %s", query.id, exc)
        return result

    if not extracted:
        return result

    return await upsert_edges(
        db,
        [CanonicalEdge(source=e.source, target=e.target, operation=e.operation) for e in extracted],
        provider=EXECUTION_PROVIDER,
        workspace_id=query.workspace_id,
        last_query_id=query.id,
    )
