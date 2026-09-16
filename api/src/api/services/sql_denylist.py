"""Object/function denials shared by both statement gates.

``sql_guard`` (the one-shot ``/queries`` allowlist) and ``statement_policy``
(the SQL-session per-statement policy) admit statement *types* and *shapes*.
Neither looks at which foreign database a statement reaches into — which was
harmless while the agent loaded only ``httpfs``/``azure``/``iceberg``, and stops
being harmless the moment it loads ``postgres`` for DuckLake:

- ``SELECT * FROM postgres_query('__ducklake_metadata_raw', '<anything>')`` is a
  plain ``SELECT`` to both gates, and runs arbitrary SQL against the catalog
  database as the agent's Postgres role. Verified reachable on DuckDB 1.5.5.
- ``SELECT * FROM ducklake_cleanup_old_files('raw', cleanup_all => true)`` deletes
  data files. DuckDB dispatches table functions from ``FROM`` as well as ``CALL``,
  so a maintenance verb reaches both gates as a plain ``SELECT``.
- DuckLake exposes its own catalog tables as ``__ducklake_metadata_<alias>``.
  ``duckdb_databases()`` does not list it, but
  ``UPDATE __ducklake_metadata_raw.cat_raw.ducklake_data_file SET record_count = 0``
  is accepted — an ``UPDATE``, which both gates permit. Writing those rows by
  hand bypasses DuckLake's transaction protocol and silently corrupts the table.

Both are denied here, unconditionally — they are wrong whether or not DuckLake
is enabled, and a rule that switches off with a feature flag is a rule nobody can
reason about.

Parsing is ``sqlglot`` (pure Python, no DuckDB connection), so I1 holds. When
sqlglot cannot parse the statement at all, a lexical fallback denies text that
merely *mentions* a denied name: precision is worth having when we can parse, and
when we cannot, refusing is better than guessing.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

# Table functions that open a connection to a foreign database. Each takes a
# connection string or an attached-database name plus SQL, so each is a way to
# run statements the gates never see. The postgres ones are the live risk (the
# agent loads that extension for DuckLake); the rest are listed because they cost
# nothing and a future extension load should not silently reopen this hole.
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

# DuckLake attaches its catalog tables under this catalog name. Reserved: a
# DuckHaven catalog slug must match ``^[a-z][a-z0-9_]*$`` (see
# ``workspace.validate_catalog_slug``), so no user-reachable catalog can ever
# begin with an underscore and this prefix cannot collide with a real name.
DUCKLAKE_METADATA_PREFIX = "__ducklake_metadata_"

# DuckLake's maintenance verbs. `ducklake_cleanup_old_files(cat, cleanup_all =>
# true)` deletes data files and `ducklake_expire_snapshots` destroys time
# travel — and DuckDB dispatches table functions from `FROM`, not only `CALL`,
# so both arrive at the gates as a plain `SELECT`. Verified executing that way
# on DuckDB 1.5.5.
#
# That would let any workspace reader run the operations DuckHaven deliberately
# does not run itself: the maintenance advisor recommends and never rewrites, and
# `applicable_in_app` is False precisely because executing them needs an
# authorization and audit design that does not exist yet.
#
# Denied by prefix rather than by name, so a verb added in a future extension
# version is refused by default instead of silently becoming reachable. It costs
# users nothing: the read-only surface is spelled as a method on the attached
# catalog (`my_lake.snapshots()`, `my_lake.table_changes(...)`), which carries no
# prefix, and time travel is `AT (VERSION => n)`, which is not a function at all.
DUCKLAKE_FUNCTION_PREFIX = "ducklake_"


class ForeignAccessDenied(Exception):
    """A statement reaches a foreign database or DuckLake's internal metadata.

    Carries a user-facing ``message`` and a short ``rule`` slug. Each caller
    re-raises it as its own gate's exception type so the HTTP shape and the
    rejection metric stay unchanged.
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

    # Check every part of every table reference, not just the catalog: a
    # three-part `cat.schema.table` puts the catalog in `.catalog`, but `USE cat`
    # parses the same name into `.name`, and a two-part reference into `.db`.
    for table in stmt.find_all(exp.Table):
        for part in (table.catalog, table.db, table.name):
            if part and part.lower().startswith(DUCKLAKE_METADATA_PREFIX):
                raise _deny_metadata(part)


def check_sql(sql: str) -> None:
    """Raise :class:`ForeignAccessDenied` if any statement in ``sql`` is denied.

    Used by gates that do their own parsing elsewhere (``sql_guard`` parses with
    DuckDB) and so have no AST to hand. A statement sqlglot cannot parse falls
    back to a lexical check, which over-matches — a string literal containing a
    denied name is refused — but only for SQL that failed to parse at all.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - unparseable falls back to the lexical check
        _check_lexically(sql)
        return

    parsed = [s for s in statements if s is not None]
    if not parsed:
        # sqlglot found nothing to look at; DuckDB's own parse decides validity.
        _check_lexically(sql)
        return
    for stmt in parsed:
        check_statement(stmt)


def _check_lexically(sql: str) -> None:
    lowered = sql.lower()
    for name in DENIED_FUNCTIONS:
        if name in lowered:
            raise _deny_function(name)
    # Checked before the metadata prefix, which also starts with "ducklake_"
    # once its leading underscores are stripped — the more specific message wins.
    if DUCKLAKE_METADATA_PREFIX in lowered:
        raise _deny_metadata(DUCKLAKE_METADATA_PREFIX + "*")
    if DUCKLAKE_FUNCTION_PREFIX in lowered:
        raise _deny_maintenance(DUCKLAKE_FUNCTION_PREFIX + "*")
