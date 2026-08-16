"""Assemble the graph the read API returns.

Walks the store, decides what the caller may see, and merges the per-provider
rows into one edge per relationship. Kept apart from ``traverse`` (which knows
only about keys and hops) and ``redact`` (which knows only about visibility) so
each of the three can be tested without standing the other two up.

Two things a caller needs in order to know how much to trust what comes back are
decided here, because this is the only place that sees both the stored rows and
the visibility verdict:

* **Completeness.** ``redact`` drops nodes outside the workspace entirely. That
  is the right call — they are out of scope, not merely unreadable — but the
  result is that a graph missing half of itself looks exactly like a graph that
  never had a second half. So the drop is *counted*, and the response says a drop
  happened without saying what was dropped.
* **Freshness.** Each stored row is one producer's observation with its own
  timestamps, and they are reported that way instead of being flattened into a
  single "last seen" that lets whichever producer ran most recently vouch for all
  the others.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.catalog import Catalog
from api.models.lineage import LineageColumnEdge, LineageEdge
from api.schemas.lineage import (
    LineageColumnOut,
    LineageEdgeOut,
    LineageGraphOut,
    LineageNodeOut,
    LineageProviderOut,
)
from api.services.lineage import traverse
from api.services.lineage.keys import internal_ref
from api.services.lineage.redact import Visibility, VisibleNode, visible_node
from api.services.lineage.times import aware_utc, is_stale

# The most column mappings one response will carry. Column detail multiplies with
# the width of the tables involved, so a graph that is perfectly reasonable at
# table level can be enormous one level down; past this the response stops being
# something a browser should be asked to hold, and `columns_truncated` says so.
MAX_COLUMN_PAIRS = 2000


async def table_lineage(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    principal_id: uuid.UUID | None,
    catalogs: list[Catalog],
    catalog: Catalog,
    schema: str,
    table: str,
    direction: str = "both",
    depth: int = 2,
    providers: list[str] | None = None,
    columns_for: set[str] | None = None,
) -> LineageGraphOut:
    """The bounded lineage graph around one table, as this caller may see it.

    ``columns_for`` names the nodes whose column detail the caller actually wants,
    and is empty by default. Column mappings are not attached otherwise: a graph
    is bounded by node count, but its column detail is bounded by the *width* of
    the tables in it, so returning everything would make the cost of asking for
    lineage depend on something the caller never sees.
    """
    root_key = internal_ref(catalog.id, schema, table).key
    walked = await traverse.walk(
        db, root_key=root_key, direction=direction, depth=depth, providers=providers
    )

    visibility = Visibility(
        db, workspace_id=workspace_id, principal_id=principal_id, catalogs=catalogs
    )

    # Resolve every node once. An endpoint appears on many edges, so the map is
    # keyed by the stored key and holds the (possibly redacted) presentation.
    resolved: dict[str, VisibleNode] = {}
    hidden = False
    for edge in walked.edges:
        for key, catalog_id, system, node_schema, node_table in (
            (
                edge.source_key,
                edge.source_catalog_id,
                edge.source_system,
                edge.source_schema,
                edge.source_table,
            ),
            (
                edge.target_key,
                edge.target_catalog_id,
                edge.target_system,
                edge.target_schema,
                edge.target_table,
            ),
        ):
            if key in resolved:
                continue
            node = await visible_node(
                visibility,
                key=key,
                catalog_id=catalog_id,
                system=system,
                schema=node_schema,
                table=node_table,
                distance=walked.distances.get(key, 0),
            )
            if node is None:
                # Out of the workspace's scope entirely. It leaves the graph, and
                # every edge through it goes with it — so record that the answer
                # is short of the truth before the evidence disappears.
                hidden = True
                continue
            resolved[key] = node

    # The root is always present, even when nothing links to it — an empty graph
    # for a real table is a meaningful answer, not a 404.
    if root_key not in resolved:
        root_node = await visible_node(
            visibility,
            key=root_key,
            catalog_id=catalog.id,
            system=None,
            schema=schema,
            table=table,
            distance=0,
        )
        if root_node is not None:
            resolved[root_key] = root_node

    pairs, columns_truncated = await _column_pairs(db, walked.edges, columns_for or set())
    merged = _merge_by_pair(walked.edges, resolved, pairs)
    root = resolved[root_key].key if root_key in resolved else root_key
    ordered = sorted(resolved.values(), key=lambda n: (n.distance, n.key))
    return LineageGraphOut(
        root=root,
        nodes=[_node_out(n) for n in ordered],
        edges=merged,
        truncated=walked.truncated,
        hidden=hidden,
        columns_truncated=columns_truncated,
    )


async def _column_pairs(
    db: AsyncSession, edges: list[LineageEdge], columns_for: set[str]
) -> tuple[dict[uuid.UUID, list[LineageColumnEdge]], bool]:
    """Column mappings for the edges touching ``columns_for``, and whether capped.

    One query over the edges the walk already found, rather than a second
    traversal: the column graph is a refinement of the table graph, so everything
    it can reach is already in hand.
    """
    if not columns_for:
        return {}, False
    wanted = [
        edge.id
        for edge in edges
        if edge.source_key in columns_for or edge.target_key in columns_for
    ]
    if not wanted:
        return {}, False

    rows = (
        (
            await db.execute(
                sa.select(LineageColumnEdge)
                .where(LineageColumnEdge.edge_id.in_(wanted))
                # Ordered so the cap takes a stable prefix rather than an
                # arbitrary one — a truncated answer that reshuffles between
                # identical requests is worse than a truncated answer.
                .order_by(
                    LineageColumnEdge.edge_id,
                    LineageColumnEdge.target_column,
                    LineageColumnEdge.source_column,
                )
                .limit(MAX_COLUMN_PAIRS + 1)
            )
        )
        .scalars()
        .all()
    )
    truncated = len(rows) > MAX_COLUMN_PAIRS
    by_edge: dict[uuid.UUID, list[LineageColumnEdge]] = {}
    for row in rows[:MAX_COLUMN_PAIRS]:
        by_edge.setdefault(row.edge_id, []).append(row)
    return by_edge, truncated


def _node_out(node: VisibleNode) -> LineageNodeOut:
    return LineageNodeOut(
        key=node.key,
        kind=node.kind,
        catalog=node.catalog,
        schema_name=node.schema,
        table=node.table,
        system=node.system,
        distance=node.distance,
    )


def _merge_by_pair(
    edges: list[LineageEdge],
    resolved: dict[str, VisibleNode],
    pairs: dict[uuid.UUID, list[LineageColumnEdge]],
) -> list[LineageEdgeOut]:
    """Collapse per-provider rows into one edge per (source, target) pair.

    Edges whose endpoints were pruned as out-of-workspace drop out here; edges
    into a *redacted* node survive, rewritten onto the opaque key so the path
    through it stays visible.

    Column mappings merge the same way the providers above them do: the same pair
    named by two producers is one mapping listing both, and two producers naming
    different pairs is two mappings, because disagreement between producers is
    information and not something to quietly resolve.
    """
    merged: dict[tuple[str, str], LineageEdgeOut] = {}
    # Column mappings, keyed within an edge so the merge can find a pair another
    # provider already contributed.
    columns: dict[tuple[str, str], dict[tuple[str, str], LineageColumnOut]] = {}
    # One clock for the whole response, so two edges of the same age cannot
    # disagree about whether they are stale.
    now = datetime.now(tz=UTC)
    after_days = settings.lineage_stale_after_days

    for edge in edges:
        source = resolved.get(edge.source_key)
        target = resolved.get(edge.target_key)
        # The endpoint was pruned above, which already recorded the graph as
        # incomplete; the edge simply cannot be drawn without it.
        if source is None or target is None:
            continue
        # Withheld when either endpoint is redacted. The query it points at is
        # readable by any workspace member and its SQL text names every table it
        # touched, so handing over the link would undo the redaction beside it —
        # the caller would read the name the node deliberately does not carry.
        withheld = source.kind == "redacted" or target.kind == "redacted"
        query_id = None if withheld else edge.last_query_id

        observation = LineageProviderOut(
            name=edge.provider,
            first_seen_at=aware_utc(edge.first_seen_at),
            last_seen_at=aware_utc(edge.last_seen_at),
            observation_count=edge.observation_count,
            stale=is_stale(edge.last_seen_at, now=now, after_days=after_days),
            column_lineage=edge.column_lineage,
        )

        pair = (source.key, target.key)
        # Withheld alongside the query link, and for the same reason: a restricted
        # table's column names are exactly the kind of detail the redaction exists
        # to keep back, and handing them over beside a node deliberately carrying
        # no name would undo it.
        if not withheld:
            slot = columns.setdefault(pair, {})
            for row in pairs.get(edge.id, ()):
                key = (row.source_column, row.target_column)
                found = slot.get(key)
                fresh = is_stale(row.last_seen_at, now=now, after_days=after_days)
                if found is None:
                    slot[key] = LineageColumnOut(
                        source_column=row.source_column,
                        target_column=row.target_column,
                        providers=[edge.provider],
                        stale=fresh,
                    )
                    continue
                if edge.provider not in found.providers:
                    found.providers.append(edge.provider)
                # One producer still confirming the mapping keeps it current, the
                # same rule the edge itself follows.
                found.stale = found.stale and fresh

        existing = merged.get(pair)
        if existing is None:
            merged[pair] = LineageEdgeOut(
                source_key=source.key,
                target_key=target.key,
                operation=edge.operation,
                providers=[observation],
                confidence=edge.confidence,
                first_seen_at=observation.first_seen_at,
                last_seen_at=observation.last_seen_at,
                observation_count=observation.observation_count,
                stale=observation.stale,
                last_query_id=query_id,
                column_lineage=edge.column_lineage,
            )
            continue
        existing.column_lineage = _better_state(existing.column_lineage, edge.column_lineage)
        existing.providers.append(observation)
        existing.observation_count += observation.observation_count
        existing.first_seen_at = min(existing.first_seen_at, observation.first_seen_at)
        existing.last_seen_at = max(existing.last_seen_at, observation.last_seen_at)
        # One producer still confirming the pair keeps the edge current; the
        # producer that stopped is marked on its own entry rather than dragging
        # the relationship down with it.
        existing.stale = existing.stale and observation.stale
        existing.last_query_id = existing.last_query_id or query_id
        existing.operation = existing.operation or edge.operation

    for pair, out in merged.items():
        out.providers.sort(key=lambda p: p.name)
        out.columns = sorted(
            columns.get(pair, {}).values(),
            key=lambda c: (c.target_column, c.source_column),
        )
        for column in out.columns:
            column.providers.sort()
    return sorted(merged.values(), key=lambda e: (e.source_key, e.target_key))


def _better_state(existing: str, incoming: str) -> str:
    """The stronger of two producers' claims about an edge's column detail.

    A producer that worked the columns out is not contradicted by one that did
    not, so ``derived`` wins; ``unsupported`` at least says somebody tried, which
    beats ``unknown``.
    """
    for state in ("derived", "unsupported"):
        if state in (existing, incoming):
            return state
    return existing
