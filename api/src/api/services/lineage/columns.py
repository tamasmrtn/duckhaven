"""Derive which source columns a target column's values came from.

A column relationship means one thing here:

    the value of ``target.d`` may be derived from the value of ``source.c``.

That is a statement about *data flow*, not about what the query happened to
mention. It is the whole reason column lineage is worth having: table lineage
already records that a source was referenced, and cannot tell a column that was
copied from a table that was only joined against or filtered on. So a column
named in a ``WHERE``, a ``HAVING``, or a ``JOIN ... ON`` contributes nothing —
those decide *which rows* survive, not what any output value is. A column named
in a projection, an expression, an aggregate's argument, or a ``CASE`` condition
does contribute, because each of those can change the value that comes out.

An edge with no column relationships is therefore a real and useful answer, not a
gap, and it is why the parent edge carries a ``column_lineage`` state saying
whether we worked the columns out at all.

**Correctness beats coverage.** Column lineage that is wrong is worse than column
lineage that is missing, because it is the kind of thing people act on. Where
this module cannot be sure, it abandons the statement and leaves the table edge
standing on its own rather than guessing: an unresolvable identifier, a ``*`` it
could not expand, a source table whose columns it could not read, an ``INSERT``
whose column list does not line up with the query. Extraction never raises at the
caller; the worst case is that a relationship stays table-level.

Semantics come from sqlglot's own lineage walker, which already draws the
data-flow/row-filter line exactly where DuckHaven wants it. They are pinned by
``tests/unit/services/lineage/test_columns.py`` all the same, so that an upgrade
which moves that line fails there rather than quietly changing what the graph
claims.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import sqlglot
from sqlglot import exp
from sqlglot.lineage import Node as LineageNode
from sqlglot.lineage import lineage as sqlglot_lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import build_scope

from api.services.lineage.extract import ParsedRef, classify, resolve_ref, statement_refs
from api.services.lineage.keys import AssetRef

logger = logging.getLogger(__name__)

# Statements whose column flow this module works out. Everything else — UPDATE,
# MERGE, DELETE — keeps its table-level edge and is marked `unsupported`, which
# says "not worked out" rather than "nothing flows". They are absent because
# sqlglot's walker does not cover assignment targets, so supporting them means
# bespoke resolution code, and they are a small share of what DuckHaven runs.
_COLUMN_OPERATIONS = frozenset({"create_table_as", "create_view", "insert"})

DERIVED = "derived"
UNSUPPORTED = "unsupported"
UNKNOWN = "unknown"

# A statement reading more tables than this is not something to resolve schemas
# for one HTTP round trip at a time on the frame-receive path.
MAX_SOURCE_TABLES = 12

# One statement cannot contribute more than this many column relationships. A
# join of two very wide tables through an expression touching most of both can
# multiply out; past this point the result is not worth what it costs to store,
# and a partial set would read as authoritative.
MAX_COLUMN_PAIRS_PER_STATEMENT = 2000

# sqlglot's placeholder for an output column it could not name — an unaliased
# expression, where DuckDB's real column name is its own rendering of the
# expression and not this. Pairs into such a column are dropped: we know where
# the values came from but not what the column is called, and half of a fact is
# not a fact.
_ANONYMOUS_OUTPUT_PREFIX = "_col_"


@dataclass(frozen=True)
class ColumnPair:
    """One ``source column -> target column`` relationship."""

    source_column: str
    target_column: str


@dataclass(frozen=True)
class ColumnLineage:
    """What was established about one edge's columns.

    ``state`` is ``derived`` or ``unsupported``. ``derived`` with an empty
    ``pairs`` is the filter-only case and is a genuine answer: the source was
    read, and none of its values reached the target.
    """

    state: str
    pairs: tuple[ColumnPair, ...] = ()


class SchemaLookup(Protocol):
    """Where a table's column names come from.

    Small on purpose. Native extraction reads the catalog; an importer hands over
    whatever its producer already published. Neither needs to know about the
    other, and the extractor needs neither.
    """

    async def columns(self, ref: AssetRef) -> list[str] | None:
        """The table's column names in schema order, or ``None`` if unknown."""


class MappingSchemaLookup:
    """A lookup over column lists somebody already has.

    For producers that publish their own schemas alongside their lineage, and for
    tests.
    """

    def __init__(self, columns_by_ref: dict[AssetRef, list[str]]) -> None:
        self._columns = columns_by_ref

    async def columns(self, ref: AssetRef) -> list[str] | None:
        return self._columns.get(ref)


class CatalogSchemaLookup:
    """A lookup that reads column names from the Iceberg catalog.

    Memoised for the life of one instance and no longer. The instance is built
    per extraction and thrown away, which is the point: nothing can invalidate a
    longer-lived cache, because schema evolution happens on the agent and the
    control plane never observes it. A cache still holding a column that has since
    been dropped would make ``SELECT *`` expand into a relationship that does not
    exist — precisely the kind of confident-but-wrong metadata this module refuses
    to produce elsewhere.

    Failures are ``None``, not exceptions: a catalog that will not answer costs
    the statement its column detail, nothing more.
    """

    def __init__(self, polaris, catalogs_by_id: dict[uuid.UUID, str]) -> None:
        self._polaris = polaris
        self._polaris_names = catalogs_by_id
        self._cache: dict[AssetRef, list[str] | None] = {}

    async def columns(self, ref: AssetRef) -> list[str] | None:
        if ref in self._cache:
            return self._cache[ref]
        result = await self._load(ref)
        self._cache[ref] = result
        return result

    async def _load(self, ref: AssetRef) -> list[str] | None:
        if ref.catalog_id is None:
            return None
        polaris_name = self._polaris_names.get(ref.catalog_id)
        if polaris_name is None:
            return None
        try:
            table = await self._polaris.get_table(polaris_name, ref.schema, ref.table)
        except Exception as exc:
            logger.debug("Column lineage: no schema for %s.%s: %s", ref.schema, ref.table, exc)
            return None
        return [c.name for c in (table.columns or [])]


def _ref_of(table: exp.Table) -> ParsedRef:
    return ParsedRef(catalog=table.catalog or None, schema=table.db or None, table=table.name)


def _leaf_columns(node: LineageNode) -> list[tuple[exp.Table, str]]:
    """Every real table column an output column's value can come from.

    A leaf whose source is not an ``exp.Table`` is a literal, a function call, or
    something sqlglot could not tie to a relation. It contributes nothing rather
    than invalidating the statement: ``SELECT 1 AS x, a FROM t`` should still
    record where ``a`` came from.
    """
    found: list[tuple[exp.Table, str]] = []

    def walk(current: LineageNode) -> None:
        if not current.downstream:
            if isinstance(current.source, exp.Table):
                # `name` is "<relation alias>.<column>"; the alias is whatever the
                # query called it, so only the column part is ours to keep — the
                # table identity comes from `source`, which is already resolved.
                found.append((current.source, current.name.split(".")[-1].strip('"')))
            return
        for child in current.downstream:
            walk(child)

    walk(node)
    return found


def _target_columns(stmt: exp.Expression, operation: str, outputs: list[str]) -> list[str] | None:
    """What the statement's output columns are called in the target.

    ``None`` means they cannot be established, which abandons the statement.
    """
    if operation in ("create_table_as", "create_view"):
        # The query's own output names are the created table's column names.
        return outputs
    # INSERT. An explicit column list names the targets directly, and is the only
    # case where position carries meaning — so a length mismatch is a refusal
    # rather than a zip that silently drops the tail.
    into = stmt.this
    if isinstance(into, exp.Schema) and into.expressions:
        declared = [c.name for c in into.expressions if isinstance(c, exp.Identifier | exp.Column)]
        if len(declared) != len(outputs):
            return None
        return declared
    return None


async def _statement_schema(
    source_refs: list[ParsedRef],
    *,
    active_catalog: str | None,
    catalog_ids: dict[str, uuid.UUID],
    schemas: SchemaLookup,
) -> dict | None:
    """A sqlglot schema map for the statement's sources, or ``None`` if incomplete.

    Incomplete has to mean "no column lineage for this statement": a partially
    known schema expands ``SELECT *`` into whichever columns happened to be
    readable, which looks exactly like a complete answer and is not one.
    """
    wanted: dict[tuple[str, str, str], AssetRef] = {}
    for ref in source_refs:
        resolved = resolve_ref(ref, active_catalog=active_catalog, catalog_ids=catalog_ids)
        if resolved is None:
            # A system catalog or an unknown slug: never a lineage asset, so it is
            # not a hole in the schema either.
            continue
        slug = ref.catalog or active_catalog
        if slug is None:
            continue
        wanted[(slug, resolved.schema, resolved.table)] = resolved

    if not wanted:
        return None
    if len(wanted) > MAX_SOURCE_TABLES:
        _record_skip("too_many_sources")
        return None

    mapping: dict = {}
    for (slug, schema, table), ref in wanted.items():
        columns = await schemas.columns(ref)
        if not columns:
            _record_skip("unresolved_schema")
            return None
        mapping.setdefault(slug, {}).setdefault(schema, {})[table] = dict.fromkeys(
            columns, "UNKNOWN"
        )
    return mapping


def _record_skip(reason: str) -> None:
    from api.metrics import record_lineage_column_skip

    record_lineage_column_skip(reason)


async def _statement_columns(
    stmt: exp.Expression,
    operation: str,
    *,
    active_catalog: str | None,
    catalog_ids: dict[str, uuid.UUID],
    schemas: SchemaLookup,
) -> dict[tuple[str, str], list[ColumnPair]] | None:
    """Column pairs for one statement, keyed by ``(source_key, target_key)``.

    ``None`` abandons the statement. An empty dict is a real answer: the query
    established relationships at table level but moved no column's values.
    """
    query = stmt.expression
    if query is None:
        return None

    target_refs, source_refs = statement_refs(stmt)
    if not target_refs or not source_refs:
        return None
    target = resolve_ref(target_refs[0], active_catalog=active_catalog, catalog_ids=catalog_ids)
    if target is None:
        return None

    return await _derive(
        query,
        target=target,
        source_refs=source_refs,
        rename=lambda names: _target_columns(stmt, operation, names),
        active_catalog=active_catalog,
        catalog_ids=catalog_ids,
        schemas=schemas,
    )


async def columns_for_query(
    sql: str,
    *,
    target: AssetRef,
    active_catalog: str | None,
    catalog_ids: dict[str, uuid.UUID],
    schemas: SchemaLookup,
) -> dict[tuple[str, str], list[ColumnPair]] | None:
    """Column pairs for a bare query that is known to build ``target``.

    The same work :func:`columns_for_sql` does, for producers whose artifact holds
    the query without the statement around it — a dbt model's compiled SQL is a
    ``SELECT``, and it is the project that says which relation it materialises
    into. The query's own output names are the target's column names, exactly as
    they are for ``CREATE TABLE AS``.

    ``None`` means nothing could be established.
    """
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return None
    if not isinstance(parsed, exp.Query):
        return None

    _, source_refs = statement_refs(parsed)
    if not source_refs:
        return None
    try:
        return await _derive(
            parsed,
            target=target,
            source_refs=source_refs,
            rename=lambda names: names,
            active_catalog=active_catalog,
            catalog_ids=catalog_ids,
            schemas=schemas,
        )
    except Exception:
        logger.exception("Column lineage extraction failed for a provider query")
        return None


async def _derive(
    query: exp.Expression,
    *,
    target: AssetRef,
    source_refs: list[ParsedRef],
    rename,
    active_catalog: str | None,
    catalog_ids: dict[str, uuid.UUID],
    schemas: SchemaLookup,
) -> dict[tuple[str, str], list[ColumnPair]] | None:
    """Trace one query's outputs back to the source columns that feed them.

    ``rename`` turns the query's own output names into the names those columns
    have in the target, which is the only part that differs between a statement
    that declares them (``INSERT ... (a, b)``) and one where the projection is
    already the answer.
    """
    # Two passes, because most statements do not need a schema at all. sqlglot can
    # attribute unqualified columns whenever there is only one relation in scope,
    # and qualified ones whatever the shape — so only `SELECT *` and a
    # multi-source query with a bare column name have to cost a catalog read.
    resolved = _walk(query, active_catalog=active_catalog, schema=None)
    if resolved is None:
        mapping = await _statement_schema(
            source_refs,
            active_catalog=active_catalog,
            catalog_ids=catalog_ids,
            schemas=schemas,
        )
        if mapping is None:
            return None
        resolved = _walk(query, active_catalog=active_catalog, schema=mapping)
        if resolved is None:
            _record_skip("unresolved_columns")
            return None

    # Two outputs called the same thing. DuckDB accepts this and disambiguates by
    # suffixing the later one — `SELECT * FROM a JOIN b` over a shared `id` builds
    # a table with `id` and `id_1` — so the names in hand are not the names in the
    # catalog, and one of the two relationships has already collapsed into the
    # other. Neither half of that is worth recording.
    if len(set(resolved.names)) != len(resolved.names):
        _record_skip("duplicate_output_columns")
        return None

    names = rename(resolved.names)
    if names is None:
        _record_skip("arity_mismatch")
        return None

    pairs: dict[tuple[str, str], list[ColumnPair]] = {}
    seen: set[tuple[str, str, str]] = set()
    total = 0
    for target_column, output_name in zip(names, resolved.names, strict=True):
        node = resolved.outputs.get(output_name)
        if node is None:
            continue
        if target_column.startswith(_ANONYMOUS_OUTPUT_PREFIX):
            # We know where the values came from but not what the column ended up
            # being called. Drop this one column and keep the rest of the
            # statement, rather than inventing a name DuckDB did not use.
            continue
        for table, source_column in _leaf_columns(node):
            source = resolve_ref(
                _ref_of(table), active_catalog=active_catalog, catalog_ids=catalog_ids
            )
            if source is None or source.key == target.key:
                continue
            key = (source.key, source_column, target_column)
            if key in seen:
                continue
            seen.add(key)
            total += 1
            if total > MAX_COLUMN_PAIRS_PER_STATEMENT:
                _record_skip("too_many_pairs")
                return None
            pairs.setdefault((source.key, target.key), []).append(
                ColumnPair(source_column=source_column, target_column=target_column)
            )
    return pairs


@dataclass(frozen=True)
class _Resolved:
    """One query's projection, resolved.

    ``names`` is the projection *in order and with duplicates intact*, which
    ``outputs`` cannot be — it is keyed by column name, so two outputs called the
    same thing collapse into one. Keeping both is what makes that collapse
    detectable instead of silent.
    """

    outputs: dict[str, LineageNode]
    names: list[str]


def _walk(
    query: exp.Expression, *, active_catalog: str | None, schema: dict | None
) -> _Resolved | None:
    """Resolve a query's projection, or ``None`` if it cannot be trusted.

    Qualification happens here rather than inside the lineage walker so that the
    qualified tree can be inspected before it is walked — a surviving ``*`` means
    the schema was not there to expand it — and so the resulting scope can be
    handed to the walker instead of making it qualify all over again.

    ``column=None`` then walks the whole projection in one pass with a shared
    cache: cheaper than a call per column, and the only way to be sure every
    output was resolved against the same view of the query.

    ``validate_qualify_columns`` is on deliberately. sqlglot's default lets an
    identifier it cannot place through unresolved, which here would attribute a
    column edge to whichever relation happened to be in scope. ``identify`` is off
    to match, so column names come back bare rather than quoted.
    """
    from api.services.workspace import DEFAULT_SCHEMA

    try:
        qualified = qualify(
            query.copy(),
            schema=schema,
            dialect="duckdb",
            db=DEFAULT_SCHEMA,
            catalog=active_catalog,
            validate_qualify_columns=True,
            identify=False,
        )
    except Exception:
        return None
    if next(qualified.find_all(exp.Star), None) is not None:
        return None
    if not isinstance(qualified, exp.Query):
        return None

    scope = build_scope(qualified)
    if scope is None:
        return None
    try:
        outputs = sqlglot_lineage(None, qualified, scope=scope, dialect="duckdb")
    except Exception:
        return None
    if not isinstance(outputs, dict):
        return None
    return _Resolved(outputs=outputs, names=[e.alias_or_name for e in qualified.selects])


async def columns_for_sql(
    sql: str,
    *,
    active_catalog: str | None,
    catalog_ids: dict[str, uuid.UUID],
    schemas: SchemaLookup,
) -> dict[tuple[str, str], ColumnLineage]:
    """The column-level detail for every edge the given SQL establishes.

    Keyed by ``(source_key, target_key)`` so the caller can line the result up
    against :func:`~api.services.lineage.extract.edges_from_sql` without this
    module and that one having to agree on anything but asset keys. Edges this
    returns which that one did not are the caller's to drop — the table graph has
    to stay a correct coarsening of the column graph.

    Never raises. A statement that cannot be parsed, cannot be resolved, or is not
    a shape this understands is simply absent or marked ``unsupported``.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception:
        return {}

    out: dict[tuple[str, str], ColumnLineage] = {}
    for stmt in statements:
        if stmt is None:
            continue
        operation = classify(stmt)
        if operation is None:
            continue
        if operation not in _COLUMN_OPERATIONS:
            _record_skip("unsupported_operation")
            _mark_unsupported(stmt, out, active_catalog=active_catalog, catalog_ids=catalog_ids)
            continue
        try:
            derived = await _statement_columns(
                stmt,
                operation,
                active_catalog=active_catalog,
                catalog_ids=catalog_ids,
                schemas=schemas,
            )
        except Exception:
            # The graph losing column detail is a much smaller problem than
            # lineage extraction failing outright, which would cost the table
            # edge too.
            logger.exception("Column lineage extraction failed")
            derived = None

        if derived is None:
            _mark_unsupported(stmt, out, active_catalog=active_catalog, catalog_ids=catalog_ids)
            continue
        for pair_key, pairs in derived.items():
            _merge(out, pair_key, DERIVED, pairs)
        # Sources that contributed no columns are still part of the answer: the
        # statement read them and nothing flowed. Recording that is the point.
        for pair_key in _edge_keys(stmt, active_catalog=active_catalog, catalog_ids=catalog_ids):
            if pair_key not in derived:
                _merge(out, pair_key, DERIVED, [])
    return out


def _edge_keys(
    stmt: exp.Expression, *, active_catalog: str | None, catalog_ids: dict[str, uuid.UUID]
) -> list[tuple[str, str]]:
    """Every ``(source_key, target_key)`` this statement relates, columns aside."""
    target_refs, source_refs = statement_refs(stmt)
    keys: list[tuple[str, str]] = []
    for target_ref in target_refs:
        target = resolve_ref(target_ref, active_catalog=active_catalog, catalog_ids=catalog_ids)
        if target is None:
            continue
        for source_ref in source_refs:
            source = resolve_ref(source_ref, active_catalog=active_catalog, catalog_ids=catalog_ids)
            if source is None or source.key == target.key:
                continue
            keys.append((source.key, target.key))
    return keys


def _mark_unsupported(
    stmt: exp.Expression,
    out: dict[tuple[str, str], ColumnLineage],
    *,
    active_catalog: str | None,
    catalog_ids: dict[str, uuid.UUID],
) -> None:
    for pair_key in _edge_keys(stmt, active_catalog=active_catalog, catalog_ids=catalog_ids):
        _merge(out, pair_key, UNSUPPORTED, [])


def _merge(
    out: dict[tuple[str, str], ColumnLineage],
    key: tuple[str, str],
    state: str,
    pairs: list[ColumnPair],
) -> None:
    """Fold one statement's verdict about an edge into the script's.

    A script can relate the same pair twice. ``derived`` wins over ``unsupported``
    because the pairs it carries are established fact, and the statement that
    could not be read says nothing that contradicts them.
    """
    existing = out.get(key)
    if existing is None:
        out[key] = ColumnLineage(state=state, pairs=tuple(pairs))
        return
    merged_state = DERIVED if DERIVED in (existing.state, state) else existing.state
    combined = list(existing.pairs)
    for pair in pairs:
        if pair not in combined:
            combined.append(pair)
    out[key] = ColumnLineage(state=merged_state, pairs=tuple(combined))
