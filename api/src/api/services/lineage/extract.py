"""Derive lineage from the SQL DuckHaven executed.

Runs in the control plane, over the statement text already persisted on
:class:`~api.models.query.Query`. That placement is deliberate and was checked
against the alternative: DuckDB's own facilities cannot supply lineage on their
own. ``EXPLAIN (FORMAT json)`` names the tables a statement *reads*, fully
qualified, but reports an empty ``extra_info`` for ``BATCH_CREATE_TABLE_AS``,
``BATCH_INSERT``, ``UPDATE`` and ``DELETE`` — the write target is never in the
plan — produces a completely empty plan for ``CREATE VIEW``, and dissolves views
into their base tables so a view can never appear as a node. ``json_serialize_sql``
refuses everything except ``SELECT``. A parser is therefore required regardless,
and once you have one it does the whole job better, views included.

sqlglot is already an API dependency parsing these same statements for grant
checks, so this module adds a parse of text the control plane has parsed before —
it opens no DuckDB connection and executes nothing (invariant I1).

Unlike :func:`~api.services.grants.extract_table_refs`, which fails *closed*
because an unparseable query must never slip past a grant check, extraction here
fails *open*: a statement that cannot be parsed simply yields no edges. A missing
edge is safe, and anything dangerous was already rejected upstream.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from api.services.grants import METADATA_SCHEMAS, SYSTEM_CATALOGS, target_tables
from api.services.lineage.keys import AssetRef


class LineageParseError(Exception):
    """The statement could not be parsed, so no lineage could be derived.

    Distinct from "parsed fine and established nothing", which is the common and
    entirely normal case: a parse failure signals a parser or dialect gap worth
    counting, an empty result does not.
    """


# Statement -> operation label. Reads are absent on purpose: a SELECT touches
# tables but moves no data into one, so it produces access history, not lineage.
_VIEW_KINDS = frozenset({"VIEW"})
_TABLE_KINDS = frozenset({"TABLE"})


@dataclass(frozen=True)
class ExtractedEdge:
    """One ``source -> target`` pair recovered from a statement."""

    source: AssetRef
    target: AssetRef
    operation: str


@dataclass(frozen=True)
class _ParsedRef:
    """A table name as written, before catalog/schema defaults are applied."""

    catalog: str | None
    schema: str | None
    table: str


def classify(stmt: exp.Expression) -> str | None:
    """The lineage operation a statement performs, or ``None`` if it performs none.

    ``CREATE TABLE`` without a query body (a bare column list) is not lineage:
    it declares a shape, it does not derive data from anywhere.
    """
    if isinstance(stmt, exp.Create):
        kind = (stmt.kind or "").upper()
        if stmt.expression is None:
            return None
        if kind in _VIEW_KINDS:
            return "create_view"
        if kind in _TABLE_KINDS:
            return "create_table_as"
        return None
    if isinstance(stmt, exp.Insert):
        return "insert"
    if isinstance(stmt, exp.Update):
        return "update"
    if isinstance(stmt, exp.Merge):
        return "merge"
    if isinstance(stmt, exp.Delete):
        return "delete"
    return None


def _statement_refs(stmt: exp.Expression) -> tuple[list[_ParsedRef], list[_ParsedRef]]:
    """Split one statement's table references into (targets, sources).

    Mirrors the filtering :func:`~api.services.grants.extract_table_refs` applies —
    in-query CTE aliases are not tables, and a table *function* parses as an
    ``exp.Table`` with an empty name — but keeps the statement grouping that
    function flattens away. Lineage needs each target paired with *its own*
    sources; a flattened list of refs from a multi-statement script cannot say
    which source fed which target.
    """
    cte_names = {c.alias_or_name for c in stmt.find_all(exp.CTE)}
    targets = target_tables(stmt)
    target_ids = {id(t) for t in targets}

    target_refs: list[_ParsedRef] = []
    source_refs: list[_ParsedRef] = []
    for table in stmt.find_all(exp.Table):
        if not table.name:
            continue  # table function, not a catalog object
        catalog = table.catalog or None
        schema = table.db or None
        if catalog is None and schema is None and table.name in cte_names:
            continue
        ref = _ParsedRef(catalog=catalog, schema=schema, table=table.name)
        if id(table) in target_ids:
            target_refs.append(ref)
        else:
            source_refs.append(ref)
    return target_refs, source_refs


def _resolve(
    ref: _ParsedRef, *, active_catalog: str | None, catalog_ids: dict[str, uuid.UUID]
) -> AssetRef | None:
    """Apply the catalog/schema defaults and resolve the slug to a catalog id.

    Uses exactly the rule ``assert_query_access`` applies, so a name means the
    same thing to the grant check and to the graph. An unknown slug yields
    ``None``: the reference is dropped rather than guessed at, because a wrong
    edge is worse than a missing one.
    """
    from api.services.workspace import DEFAULT_SCHEMA

    slug = ref.catalog or active_catalog
    schema = ref.schema or DEFAULT_SCHEMA
    if slug is None:
        return None
    if slug in SYSTEM_CATALOGS or schema in METADATA_SCHEMAS:
        return None  # discovery surfaces are not lineage assets
    catalog_id = catalog_ids.get(slug)
    if catalog_id is None:
        return None
    return AssetRef(schema=schema, table=ref.table, catalog_id=catalog_id)


def edges_from_sql(
    sql: str, *, active_catalog: str | None, catalog_ids: dict[str, uuid.UUID]
) -> list[ExtractedEdge]:
    """Every dataset relationship the given SQL establishes.

    ``catalog_ids`` maps catalog slug to ``catalogs.id`` for the catalogs the
    workspace attaches. Returns an empty list when the SQL performs no write or
    references nothing resolvable; raises :class:`LineageParseError` when it
    cannot be parsed at all, which the caller counts and swallows.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:
        raise LineageParseError(str(exc)) from exc

    edges: list[ExtractedEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for stmt in statements:
        if stmt is None:
            continue
        operation = classify(stmt)
        if operation is None:
            continue
        target_refs, source_refs = _statement_refs(stmt)
        if not target_refs or not source_refs:
            # No target, or a write with no source dataset (INSERT ... VALUES,
            # COPY FROM a file). Inventing an edge here would be a false positive.
            continue
        for target_ref in target_refs:
            target = _resolve(target_ref, active_catalog=active_catalog, catalog_ids=catalog_ids)
            if target is None:
                continue
            for source_ref in source_refs:
                source = _resolve(
                    source_ref, active_catalog=active_catalog, catalog_ids=catalog_ids
                )
                if source is None:
                    continue
                if source.key == target.key:
                    continue  # a self-edge carries no information
                dedup = (source.key, target.key, operation)
                if dedup in seen:
                    continue
                seen.add(dedup)
                edges.append(ExtractedEdge(source=source, target=target, operation=operation))
    return edges
