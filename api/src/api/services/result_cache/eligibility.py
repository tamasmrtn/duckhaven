"""Which queries the result cache may serve, and which tables each one reads.

A cached result is only correct if running the query again would produce the
same rows. That holds when the query is a single read, every relation it reads
is a versioned catalog table (so "nothing changed" can be checked), and nothing
in it depends on when, where or by whom it runs. Everything here fails closed:
a query is cacheable only when each of those is shown, and every refusal carries
a short reason slug that ends up on the query row and in the metrics.

The query is parsed by DuckDB itself (`json_serialize_sql`) rather than by
sqlglot, so function and table names are exactly the ones the agent will bind.
That is parsing only — the statement is never bound or executed here, which
keeps the control plane's "never runs user SQL" invariant (I1). The same parse
tree, with source positions removed, is the query's identity: whitespace,
comments and keyword case cannot split one query into two cache entries, while
literals and identifiers stay exact.

Two things DuckDB's own metadata gets wrong for this purpose, verified on 1.5.5:
`duckdb_functions().stability` labels `current_localtimestamp`, `getvariable`
and `current_setting` CONSISTENT although their results depend on the clock or
the session, and `current_timestamp`, `current_date`, `current_user` and friends
parse as *column references* that only become functions at bind time. Both are
handled explicitly below.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any

import duckdb

from api.services.grants import METADATA_SCHEMAS, SYSTEM_CATALOGS


class Ineligible(Exception):  # noqa: N818 - it is a verdict, not an error
    """The query cannot be served from or admitted to the cache.

    ``reason`` is a stable, low-cardinality slug (a metric label)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Bare words DuckDB's binder turns into functions when no column has the name.
# They parse as COLUMN_REF, so the function deny list never sees them. A table
# with a column of one of these names is therefore never cached — a miss, not a
# wrong answer.
_SPECIAL_COLUMN_FUNCTIONS = frozenset(
    {
        "current_date",
        "current_time",
        "current_timestamp",
        "localtime",
        "localtimestamp",
        "current_user",
        "session_user",
        "user",
        "current_role",
        "current_catalog",
        "current_schema",
        "current_database",
        "current_query",
    }
)

# Functions whose result depends on the clock, the session or the caller but that
# `duckdb_functions()` labels CONSISTENT (or not at all).
_DENY_OVERRIDES = frozenset(
    {
        "current_localtimestamp",
        "getvariable",
        "current_setting",
        "current_user",
        "session_user",
        "user",
        "current_role",
        "current_query",
        "current_query_id",
        "current_catalog",
        "current_database",
        "current_schema",
        "current_schemas",
        "txid_current",
        "today",
    }
)

# Table functions that read nothing outside the query itself. Every other table
# function either reads storage the cache cannot version (`read_parquet`,
# `iceberg_scan`) or reports engine state (`duckdb_tables`).
_ALLOWED_TABLE_FUNCTIONS = frozenset({"range", "generate_series", "unnest"})

# DuckDB's TableReferenceType names, as they appear in the serialized tree. Only
# the first group can be versioned or carries no data of its own.
_ALLOWED_RELATIONS = frozenset(
    {"BASE_TABLE", "JOIN", "SUBQUERY", "TABLE_FUNCTION", "EXPRESSION_LIST", "EMPTY"}
)
_OTHER_RELATIONS = frozenset(
    {"PIVOT", "SHOW_REF", "COLUMN_DATA", "DELIM_GET", "BOUND_TABLE_REF", "CTE", "INVALID"}
)
_QUERY_NODES = frozenset({"SELECT_NODE", "SET_OPERATION_NODE", "RECURSIVE_CTE_NODE", "CTE_NODE"})

_WORD = re.compile(r"[a-z_][a-z0-9_]*")


@dataclass(frozen=True)
class RawRef:
    """A relation as written in the query, before name resolution."""

    catalog: str
    schema: str
    table: str
    # An unqualified name that is also a CTE name. Which one a given occurrence
    # binds to depends on scope (a non-recursive CTE's own body reads the real
    # table), so it is resolved as a real table *if one exists*: an extra
    # dependency can only cost a miss, a missing one would serve stale rows.
    cte_shadowed: bool


@dataclass(frozen=True)
class Analysis:
    canonical: str
    refs: tuple[RawRef, ...]


@dataclass(frozen=True)
class TableRef:
    """A relation resolved to the catalog it binds in."""

    catalog: str
    schema: str
    table: str
    # True for a CTE-shadowed name: absent from the catalog is fine.
    optional: bool = False


class _Functions:
    """The DuckDB function catalog, read once: which names exist and which are
    unsafe to cache. A private connection, used under a lock, because DuckDB
    connections are not safe to share between threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = None
        self.known: frozenset[str] = frozenset()
        self.denied: frozenset[str] = frozenset()

    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect()
            rows = self._conn.execute(
                "SELECT lower(function_name), stability, lower(coalesce(macro_definition, '')) "
                "FROM duckdb_functions()"
            ).fetchall()
            self.known = frozenset(r[0] for r in rows)
            denied = {
                name
                for name, stability, _ in rows
                if stability in ("VOLATILE", "CONSISTENT_WITHIN_QUERY")
            }
            denied |= _DENY_OVERRIDES
            # A macro that calls a denied function is denied too (`ago` is
            # `current_timestamp - i`). Repeat until nothing new is found, for
            # macros built on macros.
            macros = [(name, body) for name, _, body in rows if body]
            changed = True
            while changed:
                changed = False
                for name, body in macros:
                    if name in denied:
                        continue
                    words = set(_WORD.findall(body))
                    if words & (denied | _SPECIAL_COLUMN_FUNCTIONS):
                        denied.add(name)
                        changed = True
            self.denied = frozenset(denied)
        return self._conn

    def serialize(self, sql: str) -> dict[str, Any]:
        with self._lock:
            conn = self.connection()
            return json.loads(conn.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])


_functions = _Functions()


def _strip_locations(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_locations(v) for k, v in node.items() if k != "query_location"}
    if isinstance(node, list):
        return [_strip_locations(v) for v in node]
    return node


def _check_function(node: dict[str, Any], *, window: bool) -> None:
    name = str(node.get("function_name") or "").lower()
    # The parser itself qualifies some built-ins (`[1, 2]` becomes
    # `main.list_value`), so `main`/`system` qualification is allowed; anything
    # else reaches a catalog's own functions, which the API's DuckDB cannot see.
    # A DuckLake macro that shadows a built-in *unqualified* is covered by the
    # catalog's routines version, not here.
    if node.get("catalog") not in ("", "system", None) or node.get("schema") not in (
        "",
        "main",
        "pg_catalog",
        None,
    ):
        raise Ineligible("unknown_function")
    if name in _functions.denied:
        raise Ineligible("volatile_function")
    if not window and name not in _functions.known:
        raise Ineligible("unknown_function")


def _walk(node: Any, refs: list[RawRef], ctes: set[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, refs, ctes)
        return
    if not isinstance(node, dict):
        return

    if node.get("sample"):
        raise Ineligible("sample")
    cte_map = node.get("cte_map")
    if isinstance(cte_map, dict):
        for entry in cte_map.get("map") or []:
            ctes.add(str(entry.get("key", "")).lower())

    kind = node.get("class")
    if kind == "FUNCTION":
        _check_function(node, window=False)
    elif kind == "WINDOW":
        _check_function(node, window=True)
    elif kind == "PARAMETER":
        raise Ineligible("parameter")
    elif kind == "COLUMN_REF":
        names = node.get("column_names") or []
        if len(names) == 1 and str(names[0]).lower() in _SPECIAL_COLUMN_FUNCTIONS:
            raise Ineligible("volatile_function")
    elif kind is None and isinstance(node.get("type"), str):
        relation = node["type"]
        if relation in _OTHER_RELATIONS:
            raise Ineligible("unsupported_relation")
        if relation == "BASE_TABLE":
            if node.get("at_clause"):
                raise Ineligible("time_travel")
            refs.append(
                RawRef(
                    catalog=str(node.get("catalog_name") or ""),
                    schema=str(node.get("schema_name") or ""),
                    table=str(node.get("table_name") or ""),
                    cte_shadowed=False,
                )
            )
        elif relation == "TABLE_FUNCTION":
            function = node.get("function") or {}
            name = str(function.get("function_name") or "").lower()
            if name not in _ALLOWED_TABLE_FUNCTIONS:
                raise Ineligible("table_function")

    for value in node.values():
        _walk(value, refs, ctes)


def analyze(sql: str) -> Analysis:
    """Parse ``sql`` and decide whether it is cacheable in principle.

    Raises :class:`Ineligible` otherwise. Name resolution (which catalog each
    relation binds in) is separate, because it depends on the caller's context.
    """
    try:
        tree = _functions.serialize(sql)
    except duckdb.Error as exc:
        raise Ineligible("parse_error") from exc
    if tree.get("error"):
        # Anything but SELECT is refused by the serializer: DML, DDL, PRAGMA,
        # transaction control. A parse error lands here too.
        raise Ineligible("not_select")
    statements = tree.get("statements") or []
    if len(statements) != 1:
        raise Ineligible("multiple_statements")
    root = statements[0]
    if (root.get("node") or {}).get("type") not in _QUERY_NODES:
        raise Ineligible("not_select")

    refs: list[RawRef] = []
    ctes: set[str] = set()
    _walk(root, refs, ctes)
    marked = tuple(
        RawRef(r.catalog, r.schema, r.table, cte_shadowed=True)
        if not r.catalog and not r.schema and r.table.lower() in ctes
        else r
        for r in refs
    )
    canonical = json.dumps(_strip_locations(root), sort_keys=True, separators=(",", ":"))
    return Analysis(canonical=canonical, refs=marked)


def resolve(
    analysis: Analysis,
    *,
    attached: set[str],
    current_catalog: str,
    current_schema: str,
) -> list[TableRef]:
    """Bind each relation to the catalog it reads, the way the agent's `USE`
    context does. Deduplicated and sorted, so the order of appearance never
    matters.

    Raises :class:`Ineligible` for a system catalog, a catalog the workspace does
    not attach, or a two-part name whose first part is also a catalog name
    (`a.b` could be schema `a` in the current catalog or catalog `a`).
    """
    if current_catalog in SYSTEM_CATALOGS:
        raise Ineligible("system_catalog")
    if current_catalog not in attached:
        raise Ineligible("unknown_catalog")
    resolved: set[TableRef] = set()
    for ref in analysis.refs:
        if ref.catalog:
            if ref.catalog in SYSTEM_CATALOGS or ref.schema in METADATA_SCHEMAS:
                raise Ineligible("system_catalog")
            if ref.catalog not in attached:
                raise Ineligible("unknown_catalog")
            resolved.add(TableRef(ref.catalog, ref.schema, ref.table))
        elif ref.schema:
            if ref.schema in METADATA_SCHEMAS:
                raise Ineligible("system_catalog")
            if ref.schema in attached or ref.schema in SYSTEM_CATALOGS:
                raise Ineligible("ambiguous_name")
            resolved.add(TableRef(current_catalog, ref.schema, ref.table))
        else:
            resolved.add(
                TableRef(current_catalog, current_schema, ref.table, optional=ref.cte_shadowed)
            )
    # A name both required and optional (written once qualified, once bare) is
    # required.
    required = {(r.catalog, r.schema, r.table) for r in resolved if not r.optional}
    return sorted(
        (r for r in resolved if not (r.optional and (r.catalog, r.schema, r.table) in required)),
        key=lambda r: (r.catalog, r.schema, r.table, r.optional),
    )
