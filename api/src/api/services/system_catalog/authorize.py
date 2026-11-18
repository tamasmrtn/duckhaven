"""Who may read the ``duckhaven`` system catalog in SQL, and how a statement may name it.

An agent attaches the store's database read-only, as its one reader login, for a
statement that reads it (see ``attach``). Every path a statement reaches an agent
by checks it here first, through ``grants.assert_query_access``:

- Reading it takes both ``audit:read`` and ``queries:admin``, held by everyone the
  query runs for. One login reads every table, and a SQL session keeps a catalog
  attached once a statement has attached it, so the permission is for the catalog
  as a whole rather than per table.
- Only a ``SELECT`` may name it, and only as ``duckhaven.<schema>.<table>`` for one
  of its three tables. A write could copy it somewhere others can read, a ``USE``
  or ``SET search_path`` would leave later unqualified names resolving inside it
  unchecked, and the attachment also shows the database's own ``pg_catalog``,
  ``information_schema`` and ``public`` schemas.
- It is never the current catalog.

Names are found with DuckDB's own tokenizer and parser, so a quoted, oddly cased
or commented name means here what it means to the agent. A workspace that still
has a catalog of its own named ``duckhaven``, from before the name was reserved,
reads that catalog instead, under that catalog's grants.
"""

from __future__ import annotations

import functools
import json
import re
import threading
import uuid
from typing import Any

import duckdb
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.services.grants import GrantDenied
from api.services.permissions import Permission
from api.services.rbac import principal_permissions
from duckhaven_shared.system_catalog import SYSTEM_CATALOG_ALIAS

TABLES: frozenset[tuple[str, str]] = frozenset(
    {("access", "audit"), ("access", "grants"), ("query", "history")}
)
REQUIRED_PERMISSIONS = frozenset({Permission.AUDIT_READ.value, Permission.QUERIES_ADMIN.value})

NEEDS_PERMISSIONS = (
    "Reading the duckhaven system catalog needs the audit:read and queries:admin permissions."
)
READ_ONLY = "The duckhaven system catalog is read-only: only a SELECT may name it."
FULL_NAMES = (
    "Name the duckhaven system catalog's tables in full: duckhaven.access.audit, "
    "duckhaven.access.grants or duckhaven.query.history."
)
NOT_CURRENT = (
    "The duckhaven system catalog cannot be the current catalog or on the search path. "
    "Name its tables in full, as in duckhaven.access.audit."
)
UNPARSEABLE = "The statement names the duckhaven system catalog and could not be parsed."

# An identifier at the start of a token: quoted, with `""` for a quote, or bare.
_IDENTIFIER = re.compile(r'"((?:[^"]|"")*)"|(\w+)')


class _Parser:
    """DuckDB's parser on a private connection, used under a lock: a connection is
    not safe to share between threads, and opening one costs about 10 ms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = None

    def serialize(self, sql: str) -> dict[str, Any]:
        with self._lock:
            if self._conn is None:
                self._conn = duckdb.connect()
            row = self._conn.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
        return json.loads(row[0])


_parser = _Parser()


def _shadowed(catalogs: list[Catalog]) -> bool:
    """Whether the workspace has a catalog of its own under the system catalog's name."""
    return any(c.slug.lower() == SYSTEM_CATALOG_ALIAS for c in catalogs)


def _is_current(active_catalog: str | None) -> bool:
    return active_catalog is not None and active_catalog.lower() == SYSTEM_CATALOG_ALIAS


def _may_name_it(sql: str) -> bool:
    """A cheap superset of the statements that name it: DuckDB folds only ASCII
    case and has no escaped identifiers."""
    return SYSTEM_CATALOG_ALIAS in sql.lower()


def _mentions(text: str) -> tuple[int, bool]:
    """How many identifiers in ``text`` are the system catalog's name, and whether
    a string literal contains it. Comments are not tokens, so never count."""
    tokens = duckdb.tokenize(text)
    identifiers, in_string = 0, False
    for i, (start, kind) in enumerate(tokens):
        if kind == duckdb.token_type.identifier:
            match = _IDENTIFIER.match(text, start)
            if match is not None:
                name = match.group(2) if match.group(1) is None else match.group(1)
                identifiers += name.replace('""', '"').lower() == SYSTEM_CATALOG_ALIAS
        elif kind == duckdb.token_type.string_const:
            end = tokens[i + 1][0] if i + 1 < len(tokens) else len(text)
            in_string = in_string or _may_name_it(text[start:end])
    return identifiers, in_string


def _base_tables(node: Any, found: list[tuple[str, str, str]]) -> None:
    if isinstance(node, list):
        for item in node:
            _base_tables(item, found)
    elif isinstance(node, dict):
        if node.get("type") == "BASE_TABLE":
            found.append(
                tuple(str(node.get(k) or "") for k in ("catalog_name", "schema_name", "table_name"))
            )
        for value in node.values():
            _base_tables(value, found)


@functools.lru_cache(maxsize=64)
def _tables_read(sql: str) -> frozenset[tuple[str, str]]:
    """The system tables ``sql`` reads, as ``(schema, table)``.

    Raises :class:`GrantDenied` for any other use of the name. Cached because each
    dispatch path asks twice, once to check and once to attach.
    """
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise GrantDenied(UNPARSEABLE) from exc
    # A statement DuckDB expands, such as a PIVOT, comes back with no text or a
    # truncated rewrite of it. Only the script's own text is checked, so in a script
    # that names it, each statement's must be part of it, and hold every use of the
    # name between them.
    script = _mentions(sql)
    if any(script) and not all(s.query and s.query in sql for s in statements):
        raise GrantDenied(UNPARSEABLE)
    mentions = [_mentions(s.query) if _may_name_it(s.query) else (0, False) for s in statements]
    if sum(identifiers for identifiers, _ in mentions) != script[0]:
        raise GrantDenied(UNPARSEABLE)
    read: set[tuple[str, str]] = set()
    for statement, (identifiers, in_string) in zip(statements, mentions, strict=True):
        text = statement.query
        # `SET search_path = 'duckhaven.access'` names it in a string.
        if statement.type == duckdb.StatementType.SET and (identifiers or in_string):
            raise GrantDenied(NOT_CURRENT)
        if not identifiers:
            continue
        if statement.type != duckdb.StatementType.SELECT:
            raise GrantDenied(READ_ONLY)
        tree = _parser.serialize(text)
        if tree.get("error"):
            # `PIVOT` and `PRAGMA` are typed SELECT but do not serialize.
            raise GrantDenied(UNPARSEABLE)
        refs: list[tuple[str, str, str]] = []
        _base_tables(tree.get("statements"), refs)
        named = [
            (schema.lower(), table.lower())
            for catalog, schema, table in refs
            if catalog.lower() == SYSTEM_CATALOG_ALIAS
        ]
        # Any other use of the name: a two-part `duckhaven.audit`, an alias, a
        # column, or one of the database's own schemas.
        if len(named) != identifiers or not set(named) <= TABLES:
            raise GrantDenied(FULL_NAMES)
        read.update(named)
    return frozenset(read)


def reads_system_catalog(sql: str, catalogs: list[Catalog]) -> bool:
    """Whether dispatching ``sql`` must attach the system catalog.

    For a statement that has passed :func:`assert_may_read`.
    """
    if _shadowed(catalogs) or not _may_name_it(sql):
        return False
    try:
        return bool(_tables_read(sql))
    except GrantDenied:
        return False


async def assert_may_read(
    db: AsyncSession,
    principal_id: uuid.UUID | None,
    sql: str,
    active_catalog: str | None,
    catalogs: list[Catalog],
) -> None:
    """Raise :class:`GrantDenied` unless ``sql`` uses the system catalog, if at all,
    as the module docstring allows, for a principal who may read it.

    ``catalogs`` are the workspace's. Open workspaces pay a substring test.
    """
    if _shadowed(catalogs):
        return
    if _is_current(active_catalog):
        raise GrantDenied(NOT_CURRENT)
    if not _may_name_it(sql) or not _tables_read(sql):
        return
    held = await principal_permissions(db, principal_id) if principal_id is not None else set()
    if not may_read(held):
        raise GrantDenied(NEEDS_PERMISSIONS)


def may_read(permissions: set[str]) -> bool:
    """Whether a principal holding ``permissions`` may read the system catalog."""
    return REQUIRED_PERMISSIONS <= permissions
