"""Bounded walks over the lineage graph.

An iterative breadth-first search issuing one indexed query per level, rather
than a recursive CTE. Three reasons, in order of weight:

1. Unit tests build the schema from ``Base.metadata`` on SQLite, and a Postgres
   recursive CTE with a ``CYCLE`` clause could not be exercised there — the
   traversal rules are the part most worth testing cheaply.
2. The cycle guard, the depth cap and the node cap are all trivial in Python and
   awkward in portable SQL.
3. With depth capped at 5 against an index, five round trips cost less than the
   recursion saves.

Both caps exist because a lineage graph is exactly the kind of structure that
looks small until one hub table turns a two-hop walk into thousands of nodes. The
walk stops and says so (``truncated``) rather than quietly returning a subset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.lineage import LineageEdge

# Matches the depth the read API accepts. Deeper walks stop being navigable in a
# UI long before they stop being computable.
MAX_DEPTH = 5
# Total distinct nodes a single walk may collect before it gives up and reports
# itself truncated.
MAX_NODES = 500


@dataclass
class Walk:
    """The subgraph a traversal collected."""

    # key -> signed distance from the root: negative upstream, positive downstream.
    distances: dict[str, int] = field(default_factory=dict)
    edges: list[LineageEdge] = field(default_factory=list)
    truncated: bool = False


async def walk(
    db: AsyncSession,
    *,
    root_key: str,
    direction: str = "both",
    depth: int = 2,
    providers: list[str] | None = None,
) -> Walk:
    """Collect the subgraph within ``depth`` hops of ``root_key``.

    ``direction`` is ``"upstream"``, ``"downstream"`` or ``"both"``. Distances are
    signed so a caller can lay the graph out without re-deriving which side a node
    came from, and so ``both`` needs no second pass to disambiguate.
    """
    depth = max(1, min(depth, MAX_DEPTH))
    result = Walk(distances={root_key: 0})
    seen_edges: set[str] = set()

    if direction in ("upstream", "both"):
        await _expand(
            db,
            result,
            seen_edges,
            root_key=root_key,
            depth=depth,
            providers=providers,
            upstream=True,
        )
    if direction in ("downstream", "both"):
        await _expand(
            db,
            result,
            seen_edges,
            root_key=root_key,
            depth=depth,
            providers=providers,
            upstream=False,
        )
    return result


async def _expand(
    db: AsyncSession,
    result: Walk,
    seen_edges: set[str],
    *,
    root_key: str,
    depth: int,
    providers: list[str] | None,
    upstream: bool,
) -> None:
    """Walk one direction, level by level."""
    # Upstream follows edges *into* the frontier; downstream follows them out.
    match_column = LineageEdge.target_key if upstream else LineageEdge.source_key
    step = -1 if upstream else 1

    frontier = {root_key}
    # Visited is per-direction: a node reachable both upstream and downstream of
    # the root is legitimately two different findings, and stopping the second
    # walk because the first got there would hide half the graph.
    visited = {root_key}

    for level in range(1, depth + 1):
        if not frontier or result.truncated:
            return
        stmt = sa.select(LineageEdge).where(match_column.in_(frontier))
        if providers:
            stmt = stmt.where(LineageEdge.provider.in_(providers))
        rows = list((await db.execute(stmt)).scalars().all())

        next_frontier: set[str] = set()
        for edge in rows:
            edge_id = f"{edge.provider}|{edge.source_key}|{edge.target_key}"
            if edge_id not in seen_edges:
                seen_edges.add(edge_id)
                result.edges.append(edge)

            other = edge.source_key if upstream else edge.target_key
            if other in visited:
                continue  # cycle guard, and a diamond's shared node stays one node
            visited.add(other)
            if other not in result.distances:
                result.distances[other] = step * level
            if len(result.distances) >= MAX_NODES:
                result.truncated = True
                return
            next_frontier.add(other)
        frontier = next_frontier
