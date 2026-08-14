"""Turn a dbt ``manifest.json`` into canonical lineage edges.

Pure functions over a parsed manifest dict — no I/O, no dbt dependency, no
knowledge of the store. The manifest is the only artifact worth consuming:
``catalog.json`` carries column types and table statistics but no dependencies at
all, and ``run_results.json`` describes one invocation's timings.

**This adapter is table-level, and that is a property of the artifact, not a
shortcut.** ``manifest.json`` records column *definitions* (names, types,
descriptions) but no column-to-column derivation, so there is nothing here to
build a column graph from.

The physical object behind a node comes from the structured ``database`` /
``schema`` / ``alias`` triple rather than ``relation_name``, which is a
pre-quoted, adapter-dialect string that would have to be re-parsed to be trusted.
"""

from __future__ import annotations

from typing import Any

from api.services.lineage.ingest import CanonicalEdge
from api.services.lineage.providers import ProviderEdges
from api.services.lineage.resolve import Resolver

# Resource types that correspond to a physical relation lineage can point at.
# `test`, `unit_test`, `analysis` and `operation` produce no persistent dataset;
# `exposure` describes a downstream consumer and is the natural next addition,
# once external assets carry a URL to link out to.
_MATERIAL_NODE_TYPES = frozenset({"model", "snapshot", "seed"})


def _relation(node: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """The physical (database, schema, table) a dbt node reads or writes.

    Which field carries the physical name depends on the resource. A model,
    seed or snapshot uses ``alias``; a *source* has no ``alias`` at all and
    names its table with the required ``identifier`` — ``name`` there is the
    logical handle used by ``source('crm', 'customers')``, which frequently
    differs from the table it points at. Falling back to ``name`` for a source
    silently names a table that does not exist, so the graph gains a phantom
    node and never joins up with the real one.

    ``name`` remains the last resort, for a model with no alias set.
    """
    return (
        node.get("database"),
        node.get("schema"),
        node.get("alias") or node.get("identifier") or node.get("name"),
    )


def _is_ephemeral(node: dict[str, Any]) -> bool:
    """An ephemeral model is inlined as a CTE and has no relation of its own."""
    return (node.get("config") or {}).get("materialized") == "ephemeral"


def _index_nodes(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every resource that maps to a relation, keyed by ``unique_id``.

    Sources are always included: they are the graph's roots, and are frequently
    the endpoints that turn out to be external to DuckHaven. Ephemeral models are
    excluded — dbt compiles them into their consumers rather than building a
    table, so naming one would invent a relation that does not exist.
    """
    indexed: dict[str, dict[str, Any]] = {}
    for unique_id, node in (manifest.get("nodes") or {}).items():
        if node.get("resource_type") in _MATERIAL_NODE_TYPES and not _is_ephemeral(node):
            indexed[unique_id] = node
    for unique_id, node in (manifest.get("sources") or {}).items():
        indexed[unique_id] = node
    return indexed


def _resolve_parents(
    unique_id: str,
    *,
    all_nodes: dict[str, dict[str, Any]],
    parent_map: dict[str, Any],
    disabled: set[str],
    _seen: frozenset[str] = frozenset(),
) -> list[str]:
    """A node's parents, with ephemeral ones replaced by *their* parents.

    ``a -> e (ephemeral) -> b`` is really ``a -> b``: dbt inlines ``e`` into
    ``b``'s compiled SQL, so ``b`` genuinely reads ``a``. Stopping at ``e`` would
    name a table that was never built *and* lose the relationship that matters.
    ``_seen`` guards a malformed manifest with a dependency cycle.
    """
    parents = parent_map.get(unique_id)
    if parents is None:
        parents = ((all_nodes.get(unique_id) or {}).get("depends_on") or {}).get("nodes") or []

    out: list[str] = []
    for parent_id in parents:
        parent = all_nodes.get(parent_id)
        if parent is None or parent_id in disabled or parent_id in _seen:
            continue
        if _is_ephemeral(parent):
            out.extend(
                _resolve_parents(
                    parent_id,
                    all_nodes=all_nodes,
                    parent_map=parent_map,
                    disabled=disabled,
                    _seen=_seen | {unique_id, parent_id},
                )
            )
            continue
        out.append(parent_id)
    return out


def run_id(manifest: dict[str, Any]) -> str | None:
    """The dbt invocation that produced this manifest, used as the import batch."""
    return (manifest.get("metadata") or {}).get("invocation_id")


def edges_from_manifest(manifest: dict[str, Any], *, resolve: Resolver) -> ProviderEdges:
    """Every table-level relationship the dbt project declares.

    Reads ``parent_map`` when present — dbt precomputes it — and falls back to
    each node's own ``depends_on.nodes``. Disabled resources are excluded, as are
    parents that are not themselves relations (a model depending on a test says
    nothing about where data came from).
    """
    nodes = _index_nodes(manifest)
    disabled = set(manifest.get("disabled") or {})
    parent_map = manifest.get("parent_map") or {}
    # Ephemerals are absent from `nodes` (they have no relation) but must still
    # be walkable, so parent resolution gets an index that includes them.
    all_nodes: dict[str, dict[str, Any]] = {
        **(manifest.get("nodes") or {}),
        **(manifest.get("sources") or {}),
    }

    result = ProviderEdges()
    edges = result.edges
    skipped = result.skipped
    seen: set[tuple[str, str]] = set()

    for unique_id, node in nodes.items():
        if unique_id in disabled:
            continue
        # A source is never a target: dbt does not build it, it only ever
        # appears as the source side of somebody else's edge. Everything else
        # here is something dbt writes.
        is_source = node.get("resource_type") == "source"

        database, schema, table = _relation(node)
        target, skip = resolve.resolve(
            catalog=database,
            system=None,
            schema=schema or "",
            table=table or "",
            allow_external=False,
        )
        if target is None:
            if skip is not None and not is_source:
                skipped.append(skip)
            continue

        # Recorded before the parent check, so a model that lost its last
        # dependency still scopes reconciliation and its stale edges can be
        # pruned. Skipping it here is what let them survive forever.
        if not is_source:
            result.targets.add(target.key)

        parents = _resolve_parents(
            unique_id, all_nodes=all_nodes, parent_map=parent_map, disabled=disabled
        )
        if not parents:
            continue

        for parent_id in parents:
            parent = nodes.get(parent_id)
            if parent is None:
                continue  # a test or macro dependency, not a data dependency
            p_database, p_schema, p_table = _relation(parent)
            source, skip = resolve.resolve(
                catalog=p_database,
                system=None,
                schema=p_schema or "",
                table=p_table or "",
                # A source in a database DuckHaven does not manage is a real
                # upstream, so keep it rather than dropping the graph's root.
                allow_external=parent.get("resource_type") == "source",
            )
            if source is None:
                if skip is not None:
                    skipped.append(skip)
                continue
            if source.key == target.key:
                continue
            pair = (source.key, target.key)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(
                CanonicalEdge(source=source, target=target, operation="model", confidence="exact")
            )
    return result
