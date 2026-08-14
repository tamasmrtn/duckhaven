"""Persist lineage edges, whatever produced them.

The single write path into :class:`~api.models.lineage.LineageEdge`. Producers —
the native extractor and every importer — translate into
:class:`CanonicalEdge` and hand it here; nothing else touches the table, so
identity, deduplication and reconciliation are decided in exactly one place.

``record_execution_lineage`` is called from the agent frame handler when a query
completes, mirroring how
:func:`~api.services.maintenance.ingest.record_health_sample` persists a health
probe from the same hook. It is also what replays historical statements during a
backfill — the same function, given an older ``observed_at``, so history and live
traffic cannot drift apart.

Writes are **order-independent**. An observation carries the time it happened
rather than the time it was recorded, and merging one into an existing edge takes
the earliest first sighting, the latest last sighting, and the descriptive fields
of whichever observation is newer. Replaying six months of history in any order
therefore lands on the same rows, and a statement from March cannot make a
relationship look like it was confirmed today.

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

from api.models.catalog import Catalog
from api.models.lineage import LineageEdge
from api.models.query import Query
from api.services.lineage.extract import LineageParseError, edges_from_sql
from api.services.lineage.keys import AssetRef, asset_key
from api.services.lineage.times import aware_utc

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
    # The statement could not be parsed at all, so nothing could be derived from
    # it. Distinct from an empty result, which means it parsed and established
    # nothing — a backfill counts the two separately because only the first
    # points at a parser gap.
    parse_failed: bool = False


async def upsert_edges(
    db: AsyncSession,
    edges: list[CanonicalEdge],
    *,
    provider: str,
    provider_run_id: str | None = None,
    workspace_id: uuid.UUID | None = None,
    last_query_id: uuid.UUID | None = None,
    observed_at: datetime | None = None,
) -> IngestResult:
    """Insert or refresh each edge, returning what changed.

    ``observed_at`` is when the relationship was *observed*, which is not
    necessarily now: a backfilled statement passes the time it originally ran.
    Defaults to now, which is right for anything happening live.

    Idempotent in the sense that matters: re-asserting a relationship refreshes
    the existing row rather than duplicating it. Two providers naming the same
    pair keep two rows, because ``provider`` is part of the identity.
    """
    result = IngestResult()
    if not edges:
        return result
    # Normalised up front: `observed_at` reaches here straight off a `queries`
    # row, which SQLite hands back naive, and every comparison below mixes it
    # with a stored timestamp.
    stamp = aware_utc(observed_at) if observed_at is not None else datetime.now(tz=UTC)

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
            existing.first_seen_at = min(aware_utc(existing.first_seen_at), stamp)
            if stamp >= aware_utc(existing.last_seen_at):
                # The newest observation describes the relationship. An older one
                # arriving later — a backfill walking history, a delayed frame —
                # still counts and still widens the window, but it must not
                # overwrite what a more recent statement said, nor claim the
                # click-through to "the query that produced this".
                existing.last_seen_at = stamp
                existing.operation = edge.operation or existing.operation
                existing.confidence = edge.confidence
                if last_query_id is not None:
                    existing.last_query_id = last_query_id
                if workspace_id is not None:
                    existing.workspace_id = workspace_id
            # Bookkeeping for the reconcile pass rather than a fact about the
            # observation: it records which run last touched this row, so it is
            # always the caller's run id regardless of ordering.
            if provider_run_id is not None:
                existing.provider_run_id = provider_run_id
            existing.observation_count = (existing.observation_count or 0) + 1
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


async def rekey_table_lineage(
    db: AsyncSession,
    catalog_id: uuid.UUID,
    *,
    old_schema: str,
    old_table: str,
    new_schema: str,
    new_table: str,
) -> int:
    """Move every edge touching one address onto another, keeping its history.

    The repair behind rename survival. Lineage keys on the address
    ``(catalog, schema, table)`` because that is what makes traversal a single
    indexed equality lookup; a rename changes the address, so the edges have to
    be rewritten to follow it. Deciding *that* a rename happened is
    ``services.lineage.identity``'s job — this only carries out the move, once
    that is settled.

    Two collisions have to be survived rather than raised on, because the unique
    constraint would otherwise abort the whole transaction:

    * the new address already has an edge from the same provider to or from the
      same counterpart — the two are the same relationship seen under two names,
      so they are merged into the widest window and the summed count;
    * the move would make an edge point at itself, which happens when a table is
      renamed onto the name of something it was built from. A self-edge carries
      no information, so it is dropped, matching what extraction does.

    Returns how many rows were rewritten, merged or dropped.
    """
    if (old_schema, old_table) == (new_schema, new_table):
        return 0

    old_key = asset_key(schema=old_schema, table=old_table, catalog_id=catalog_id)
    new_key = asset_key(schema=new_schema, table=new_table, catalog_id=catalog_id)

    moving = list(
        (
            await db.execute(
                sa.select(LineageEdge).where(
                    sa.or_(
                        LineageEdge.source_key == old_key,
                        LineageEdge.target_key == old_key,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if not moving:
        return 0

    # Everything already at the new address, so a collision can be detected
    # without a query per moved edge.
    settled = {
        (e.provider, e.source_key, e.target_key): e
        for e in (
            await db.execute(
                sa.select(LineageEdge).where(
                    sa.or_(
                        LineageEdge.source_key == new_key,
                        LineageEdge.target_key == new_key,
                    )
                )
            )
        )
        .scalars()
        .all()
    }

    touched = 0
    for edge in moving:
        source_key = new_key if edge.source_key == old_key else edge.source_key
        target_key = new_key if edge.target_key == old_key else edge.target_key
        touched += 1

        if source_key == target_key:
            await db.delete(edge)
            continue

        clash = settled.get((edge.provider, source_key, target_key))
        if clash is not None:
            _absorb(clash, edge)
            await db.delete(edge)
            continue

        if edge.source_key == old_key:
            edge.source_key = source_key
            edge.source_schema = new_schema
            edge.source_table = new_table
        if edge.target_key == old_key:
            edge.target_key = target_key
            edge.target_schema = new_schema
            edge.target_table = new_table
        settled[(edge.provider, source_key, target_key)] = edge

    await db.flush()
    return touched


def _absorb(keeper: LineageEdge, other: LineageEdge) -> None:
    """Fold one edge's history into another describing the same relationship."""
    keeper.first_seen_at = min(aware_utc(keeper.first_seen_at), aware_utc(other.first_seen_at))
    if aware_utc(other.last_seen_at) >= aware_utc(keeper.last_seen_at):
        keeper.last_seen_at = aware_utc(other.last_seen_at)
        keeper.operation = other.operation or keeper.operation
        keeper.last_query_id = other.last_query_id or keeper.last_query_id
        keeper.provider_run_id = other.provider_run_id or keeper.provider_run_id
    keeper.observation_count = (keeper.observation_count or 0) + (other.observation_count or 0)


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


@dataclass(frozen=True)
class WorkspaceCatalogs:
    """Everything resolving a statement's table names needs, resolved once.

    Live extraction resolves this per completed query — one indexed lookup on a
    path that already did several. A backfill walks thousands of statements
    belonging to the same workspace, so it resolves this once per batch instead,
    which is the difference between a constant and an N+1 over query history.
    """

    catalogs: list[Catalog]
    ids: dict[str, uuid.UUID]
    default_slug: str | None


async def workspace_catalog_context(db: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceCatalogs:
    """The catalogs a workspace attaches, and which one unqualified names mean.

    One query for both, rather than reusing ``resolve_workspace_catalogs`` and
    ``get_default_catalog`` in sequence: the default is a column on the same
    attachment row the first query already reads, and this runs on the path every
    completed query takes.
    """
    from api.models.catalog import WorkspaceCatalog

    rows = list(
        await db.execute(
            sa.select(Catalog, WorkspaceCatalog.is_default)
            .join(WorkspaceCatalog, WorkspaceCatalog.catalog_id == Catalog.id)
            .where(WorkspaceCatalog.workspace_id == workspace_id)
            .order_by(Catalog.slug)
        )
    )
    catalogs = [catalog for catalog, _ in rows]
    default_slug = next((c.slug for c, is_default in rows if is_default), None)
    return WorkspaceCatalogs(
        catalogs=catalogs,
        ids={c.slug: c.id for c in catalogs},
        default_slug=default_slug,
    )


def _active_catalog(query: Query, context: WorkspaceCatalogs) -> str | None:
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
    if context.default_slug is not None:
        return context.default_slug
    return context.catalogs[0].slug if context.catalogs else None


async def record_execution_lineage(
    db: AsyncSession,
    query: Query,
    *,
    context: WorkspaceCatalogs | None = None,
    observed_at: datetime | None = None,
) -> IngestResult:
    """Derive and persist lineage for a query that completed successfully.

    Called from the agent frame handler for a query that just finished, and from
    the backfill for one that finished long ago — the same derivation either way,
    differing only in the ``observed_at`` it stamps and in whether the caller
    already resolved the workspace's catalogs.

    Never raises for a reason the caller could not act on: a statement that
    cannot be parsed, or that establishes no relationship, simply records
    nothing. ``parse_failed`` on the result distinguishes the two.
    """
    result = IngestResult()
    if not query.sql or query.origin in _INTERNAL_ORIGINS:
        return result

    if context is None:
        context = await workspace_catalog_context(db, query.workspace_id)
    if not context.ids:
        return result

    active_catalog = _active_catalog(query, context)
    try:
        extracted = edges_from_sql(
            query.sql, active_catalog=active_catalog, catalog_ids=context.ids
        )
    except LineageParseError as exc:
        from api.metrics import record_lineage_extract_failure

        record_lineage_extract_failure()
        logger.debug("Lineage extraction skipped for query %s: %s", query.id, exc)
        result.parse_failed = True
        return result

    if not extracted:
        return result

    return await upsert_edges(
        db,
        [CanonicalEdge(source=e.source, target=e.target, operation=e.operation) for e in extracted],
        provider=EXECUTION_PROVIDER,
        workspace_id=query.workspace_id,
        last_query_id=query.id,
        # When the statement actually ran, not when we got around to reading it.
        observed_at=observed_at or query.finished_at or query.started_at,
    )
