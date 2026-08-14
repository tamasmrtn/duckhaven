"""Assemble the graph the read API returns.

Walks the store, decides what the caller may see, and merges the per-provider
rows into one edge per relationship. Kept apart from ``traverse`` (which knows
only about keys and hops) and ``redact`` (which knows only about visibility) so
each of the three can be tested without standing the other two up.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.models.lineage import LineageEdge
from api.schemas.lineage import LineageEdgeOut, LineageGraphOut, LineageNodeOut
from api.services.lineage import traverse
from api.services.lineage.keys import internal_ref
from api.services.lineage.redact import Visibility, VisibleNode, visible_node


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
) -> LineageGraphOut:
    """The bounded lineage graph around one table, as this caller may see it."""
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
            if node is not None:
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

    merged = _merge_by_pair(walked.edges, resolved)
    root = resolved[root_key].key if root_key in resolved else root_key
    ordered = sorted(resolved.values(), key=lambda n: (n.distance, n.key))
    return LineageGraphOut(
        root=root,
        nodes=[_node_out(n) for n in ordered],
        edges=merged,
        truncated=walked.truncated,
    )


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


def _aware(value: datetime) -> datetime:
    """Timestamps come back naive from SQLite and aware from Postgres.

    Merging edges compares them, so they are normalised to UTC here rather than
    letting the comparison raise on one backend and not the other.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _merge_by_pair(
    edges: list[LineageEdge], resolved: dict[str, VisibleNode]
) -> list[LineageEdgeOut]:
    """Collapse per-provider rows into one edge per (source, target) pair.

    Edges whose endpoints were pruned as out-of-workspace drop out here; edges
    into a *redacted* node survive, rewritten onto the opaque key so the path
    through it stays visible.
    """
    merged: dict[tuple[str, str], LineageEdgeOut] = {}
    for edge in edges:
        source = resolved.get(edge.source_key)
        target = resolved.get(edge.target_key)
        if source is None or target is None:
            continue
        pair = (source.key, target.key)
        existing = merged.get(pair)
        if existing is None:
            merged[pair] = LineageEdgeOut(
                source_key=source.key,
                target_key=target.key,
                operation=edge.operation,
                providers=[edge.provider],
                confidence=edge.confidence,
                first_seen_at=edge.first_seen_at,
                last_seen_at=edge.last_seen_at,
                observation_count=edge.observation_count,
                last_query_id=edge.last_query_id,
            )
            continue
        if edge.provider not in existing.providers:
            existing.providers.append(edge.provider)
        existing.observation_count += edge.observation_count
        existing.first_seen_at = min(_aware(existing.first_seen_at), _aware(edge.first_seen_at))
        existing.last_seen_at = max(_aware(existing.last_seen_at), _aware(edge.last_seen_at))
        existing.last_query_id = existing.last_query_id or edge.last_query_id
        existing.operation = existing.operation or edge.operation
    for out in merged.values():
        out.providers.sort()
    return sorted(merged.values(), key=lambda e: (e.source_key, e.target_key))
