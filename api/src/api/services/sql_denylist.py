"""Object/function denials shared by both statement gates.

``sql_guard`` (the ``/queries`` allowlist) and ``statement_policy`` (SQL-session
policy) admit statement types and shapes, but not which foreign database a
statement reaches. Loading ``postgres`` for DuckLake made three holes reachable:
``postgres_query`` runs arbitrary SQL against the catalog database; DuckLake
maintenance verbs arrive as plain ``SELECT`` because DuckDB dispatches table
functions from ``FROM``; and ``__ducklake_metadata_<alias>`` accepts direct
``UPDATE``s that corrupt the catalog. All are denied unconditionally — a rule
that switches off with a feature flag is one nobody can reason about.

Parsing is ``sqlglot``, pure Python, so I1 holds. Unparseable SQL falls back to
a lexical check that over-matches; refusing beats guessing.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

# Table functions that open a connection to a foreign database, each able to run
# statements the gates never see. The postgres ones are the live risk; the rest
# are listed so a future extension load cannot silently reopen the hole.
DENIED_FUNCTIONS = frozenset(
    {
        "postgres_query",
        "postgres_scan",
        "postgres_scan_pushdown",
        "postgres_execute",
        "mysql_query",
        "mysql_execute",
        "mysql_scan",
        "sqlite_query",
        "sqlite_scan",
        "sqlite_attach",
    }
)

# Reserved: catalog slugs match ``^[a-z][a-z0-9_]*$``, so this prefix cannot
# collide with a real name.
DUCKLAKE_METADATA_PREFIX = "__ducklake_metadata_"

# DuckLake's maintenance verbs delete files or snapshots, and arrive as plain
# ``SELECT`` because DuckDB dispatches table functions from ``FROM``. Denied by
# prefix, so a verb added in a future extension is refused by default. The
# read-only surface (`catalog.snapshots()`, time travel) carries no prefix.
DUCKLAKE_FUNCTION_PREFIX = "ducklake_"


class ForeignAccessDenied(Exception):
    """A statement reaches a foreign database or DuckLake's internal metadata.

    Carries a user-facing ``message`` and a short ``rule`` slug; each caller
    re-raises it as its own gate's exception type.
    """

    def __init__(self, message: str, rule: str) -> None:
        super().__init__(message)
        self.rule = rule


def _deny_function(name: str) -> ForeignAccessDenied:
    return ForeignAccessDenied(
        f"{name}() is not permitted: it opens a connection to a foreign database.",
        "foreign_database_function",
    )


def _deny_maintenance(name: str) -> ForeignAccessDenied:
    return ForeignAccessDenied(
        f"{name}() is not permitted: DuckLake maintenance rewrites or deletes data, "
        "and DuckHaven does not run it from user SQL. Use the Lakehouse health page "
        "to see what a catalog needs.",
        "ducklake_maintenance_function",
    )


def _deny_metadata(ref: str) -> ForeignAccessDenied:
    return ForeignAccessDenied(
        f"{ref!r} is DuckLake's internal catalog metadata and is not queryable. "
        "Use the catalog browser, or query the tables themselves.",
        "ducklake_metadata_access",
    )


def check_statement(stmt: exp.Expression) -> None:
    """Raise :class:`ForeignAccessDenied` if ``stmt`` touches a denied name."""
    for node in stmt.find_all(exp.Anonymous):
        if not isinstance(node.this, str):
            continue
        name = node.this.lower()
        if name in DENIED_FUNCTIONS:
            raise _deny_function(name)
        if name.startswith(DUCKLAKE_FUNCTION_PREFIX):
            raise _deny_maintenance(name)

    # Check every part of a table reference: `cat.schema.table` puts the catalog
    # in `.catalog`, but `USE cat` parses it into `.name`, a two-part into `.db`.
    for table in stmt.find_all(exp.Table):
        for part in (table.catalog, table.db, table.name):
            if part and part.lower().startswith(DUCKLAKE_METADATA_PREFIX):
                raise _deny_metadata(part)


def check_sql(sql: str) -> None:
    """Raise :class:`ForeignAccessDenied` if any statement in ``sql`` is denied.

    For gates that parse elsewhere (``sql_guard`` uses DuckDB) and have no AST.
    Unparseable SQL falls back to the lexical check.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - unparseable falls back to the lexical check
        _check_lexically(sql)
        return

    parsed = [s for s in statements if s is not None]
    if not parsed:
        # Nothing parsed; DuckDB's own parse decides validity.
        _check_lexically(sql)
        return
    for stmt in parsed:
        check_statement(stmt)


def _check_lexically(sql: str) -> None:
    lowered = sql.lower()
    for name in DENIED_FUNCTIONS:
        if name in lowered:
            raise _deny_function(name)
    # Checked before the function prefix, which "ducklake_" also matches.
    if DUCKLAKE_METADATA_PREFIX in lowered:
        raise _deny_metadata(DUCKLAKE_METADATA_PREFIX + "*")
    if DUCKLAKE_FUNCTION_PREFIX in lowered:
        raise _deny_maintenance(DUCKLAKE_FUNCTION_PREFIX + "*")
