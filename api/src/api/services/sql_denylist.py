"""Object/function denials shared by both statement gates.

``sql_guard`` and ``statement_policy`` admit statement shapes, not which
database a statement reaches. Loading ``postgres`` for DuckLake opened three
holes: ``postgres_query`` against the catalog database, maintenance verbs
disguised as ``SELECT ... FROM ducklake_*()``, and direct writes to
``__ducklake_metadata_<alias>``. All are denied unconditionally, regardless of
feature flags.

Unparseable SQL falls back to a lexical check that over-matches on purpose.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

# Table functions that reach a foreign database. Non-postgres ones are listed so
# a future extension load cannot silently reopen the hole.
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

# Cannot collide with a catalog slug, which must start with a letter.
DUCKLAKE_METADATA_PREFIX = "__ducklake_metadata_"

# Denied by prefix so a future maintenance verb is refused by default. The
# read-only surface (`catalog.snapshots()`, time travel) has no prefix.
DUCKLAKE_FUNCTION_PREFIX = "ducklake_"


# `CHECKPOINT <catalog>` runs every DuckLake maintenance verb without the prefix.
# The gates' allowlists already refuse it only because of how sqlglot parses it;
# denied by name so the guarantee does not depend on that.
DENIED_COMMAND_HEADS = frozenset({"checkpoint"})


class ForeignAccessDenied(Exception):
    """A statement reaches a foreign database or DuckLake's internal metadata."""

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


def _deny_command(name: str) -> ForeignAccessDenied:
    return ForeignAccessDenied(
        f"{name.upper()} is not permitted: it runs DuckLake's maintenance verbs, "
        "which rewrite and delete data. Use the Lakehouse health page to see what "
        "a catalog needs.",
        "ducklake_checkpoint",
    )


def _leading_words(stmt: exp.Expression) -> list[str]:
    """The leading bare words of a statement sqlglot could not model as SQL.

    Root only, so a column named `checkpoint` in a `Select` is not matched.
    """
    if isinstance(stmt, exp.Column | exp.Alias | exp.Command):
        return stmt.sql(dialect="duckdb").lower().replace('"', " ").split()
    return []


def _deny_metadata(ref: str) -> ForeignAccessDenied:
    return ForeignAccessDenied(
        f"{ref!r} is DuckLake's internal catalog metadata and is not queryable. "
        "Use the catalog browser, or query the tables themselves.",
        "ducklake_metadata_access",
    )


def check_statement(stmt: exp.Expression) -> None:
    """Raise :class:`ForeignAccessDenied` if ``stmt`` touches a denied name."""
    # `CHECKPOINT db` and `FORCE CHECKPOINT db` put the keyword first or second.
    for word in _leading_words(stmt)[:2]:
        if word in DENIED_COMMAND_HEADS:
            raise _deny_command(word)

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

    For gates with no sqlglot AST of their own.
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
    for name in DENIED_COMMAND_HEADS:
        if re.search(rf"\b{name}\b", lowered):
            raise _deny_command(name)
