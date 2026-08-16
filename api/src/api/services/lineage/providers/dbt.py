"""Turn dbt's artifacts into canonical lineage edges.

Pure functions over parsed artifact dicts — no I/O, no dbt dependency, no
knowledge of the store. ``manifest.json`` carries the dependency graph;
``run_results.json`` describes one invocation's timings and is not used.

**dbt does not publish column-to-column derivation.** The manifest records column
*definitions* — names, types, descriptions — and nothing about which upstream
column feeds which. Column-level lineage is a hosted-platform feature rather than
something in the artifacts, so an importer that waited for dbt to hand it over
would wait forever.

What dbt *does* publish is each model's ``compiled_code``: the exact SQL it ran,
with every ``ref()`` and ``source()`` already resolved to a real relation. That is
enough, because it is the same question DuckHaven answers for its own statements —
so this adapter runs the ordinary extractor over it rather than growing a column
graph of its own. Nothing about the resulting relationships records that dbt was
involved, beyond the provider name already on the edge.

That needs each source relation's columns, which is what ``catalog.json`` (from
``dbt docs generate``) is for. Without it the import stays table-level: reading
those schemas from the catalog instead would mean a round trip per model inside a
single request, and dbt's own artifact is the better authority anyway.

The physical object behind a node comes from the structured ``database`` /
``schema`` / ``alias`` triple rather than ``relation_name``, which is a
pre-quoted, adapter-dialect string that would have to be re-parsed to be trusted.
"""

from __future__ import annotations

import asyncio
from typing import Any

from api.services.lineage.columns import (
    DERIVED,
    UNKNOWN,
    UNSUPPORTED,
    MappingSchemaLookup,
    columns_for_query,
)
from api.services.lineage.ingest import CanonicalEdge
from api.services.lineage.keys import AssetRef
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


def _schema_lookup(catalog: dict[str, Any] | None, *, resolve: Resolver) -> MappingSchemaLookup:
    """Every relation's columns, as ``dbt docs generate`` observed them.

    Keyed by resolved asset so it answers the same question the catalog would,
    without the round trips. Columns come back in ``index`` order because that is
    the order the warehouse reports them in, and a schema in a stable order is
    easier to read in a diff.
    """
    if not catalog:
        return MappingSchemaLookup({})

    columns_by_ref: dict[AssetRef, list[str]] = {}
    for section in ("nodes", "sources"):
        for entry in (catalog.get(section) or {}).values():
            meta = entry.get("metadata") or {}
            ref, _ = resolve.resolve(
                catalog=meta.get("database"),
                system=None,
                schema=meta.get("schema") or "",
                table=meta.get("name") or "",
                allow_external=False,
            )
            if ref is None:
                continue
            columns = (entry.get("columns") or {}).values()
            ordered = sorted(columns, key=lambda c: c.get("index") or 0)
            names = [c["name"] for c in ordered if c.get("name")]
            if names:
                columns_by_ref[ref] = names
    return MappingSchemaLookup(columns_by_ref)


async def _model_columns(
    node: dict[str, Any],
    target: AssetRef,
    *,
    resolve: Resolver,
    schemas: MappingSchemaLookup,
) -> dict[tuple[str, str], list] | None:
    """What flows into this model, worked out from the SQL dbt actually ran.

    ``None`` when the SQL could not be read — a construct the extractor declines,
    or a source whose columns the catalog does not carry. Callers reach here only
    once they know there is compiled SQL to read, because "nothing to try" and
    "tried and could not" are different answers. The table-level edges are
    unaffected either way.
    """
    compiled = node.get("compiled_code")
    if not compiled or not isinstance(compiled, str):
        return None
    return await columns_for_query(
        compiled,
        target=target,
        # dbt's compiled SQL fully qualifies every relation, because that is what
        # `ref()` and `source()` render to. An unqualified name in there is a
        # hardcoded one, which is exactly the kind of reference not to guess at.
        active_catalog=None,
        catalog_ids=resolve.catalog_ids,
        schemas=schemas,
    )


async def edges_from_manifest(
    manifest: dict[str, Any],
    *,
    resolve: Resolver,
    catalog: dict[str, Any] | None = None,
) -> ProviderEdges:
    """Every relationship the dbt project declares, with columns where possible.

    Reads ``parent_map`` when present — dbt precomputes it — and falls back to
    each node's own ``depends_on.nodes``. Disabled resources are excluded, as are
    parents that are not themselves relations (a model depending on a test says
    nothing about where data came from).

    ``catalog`` is a parsed ``catalog.json``. With it, each model's compiled SQL is
    traced to the columns feeding each of its own columns; without it the result is
    table-level, exactly as before.
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

    schemas = _schema_lookup(catalog, resolve=resolve)
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

        # Worked out once per model rather than once per parent: the compiled SQL
        # names every one of them, so a single pass answers for all its edges.
        # `attempted` is tracked separately because a model dbt never compiled
        # leaves nothing to read, which is not the same as reading it and failing.
        columns = None
        attempted = bool(catalog) and not is_source and bool(node.get("compiled_code"))
        if attempted:
            # Parsing a model's SQL is real CPU work, and a project can bring
            # thousands. Nothing in the parse awaits, so without this the whole
            # import holds the event loop and every other request on the replica —
            # the agent's control channel included — waits for it to finish.
            await asyncio.sleep(0)
            columns = await _model_columns(node, target, resolve=resolve, schemas=schemas)

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
                CanonicalEdge(
                    source=source,
                    target=target,
                    operation="model",
                    confidence="exact",
                    column_lineage=_column_state(columns, pair, source, attempted=attempted),
                    columns=tuple(columns.get(pair, ())) if columns is not None else (),
                )
            )
    return result


def _column_state(
    columns: dict | None, pair: tuple[str, str], source: AssetRef, *, attempted: bool
) -> str:
    """What this edge can honestly say about its columns.

    ``derived`` with no pairs is deliberate and load-bearing: dbt declares a
    dependency for anything a model refs, and this is what separates a parent
    whose data actually reaches the model from one it only filters or joins
    against.

    But that reading only holds when the extractor *could* have attributed the
    parent's columns. An external source is one it cannot resolve at all, so
    finding no pairs there says nothing about whether data flows — reporting it as
    ``derived`` would turn "we could not look" into "we looked and there is
    nothing", which is the one mistake this feature must not make. The same goes
    for a model whose SQL was there to read and could not be read; only one that
    was never compiled, leaving nothing to try, is ``unknown``.
    """
    if columns is None:
        return UNSUPPORTED if attempted else UNKNOWN
    if columns.get(pair):
        return DERIVED
    return UNSUPPORTED if source.is_external else DERIVED
