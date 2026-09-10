"""SQL allowlist enforced before dispatch (G-D8-a).

Data statements — `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE` — and
catalog DDL — `CREATE`, `ALTER`, `DROP` — are permitted; everything that
could escape the per-query sandbox (`ATTACH`/`DETACH`, `COPY`/`EXPORT`,
`INSTALL`/`LOAD`, `SET`/`PRAGMA`, `CALL`, `VACUUM`, `PREPARE`/`EXECUTE`,
transaction control, …) is rejected. Multi-statement bodies must consist
entirely of allowed statements.

The control plane uses DuckDB *as a parser*: `duckdb.extract_statements`
is purely lexical/syntactic — no execution, no storage, no extensions
loaded. D1's intent ("control plane does not run DuckDB") is preserved
because we never construct a connection, never bind to a database, and
never call `.execute()` on user SQL.
"""

from __future__ import annotations

import duckdb

_ALLOWED_TYPES = {
    duckdb.StatementType.SELECT,
    duckdb.StatementType.INSERT,
    duckdb.StatementType.UPDATE,
    duckdb.StatementType.DELETE,
    duckdb.StatementType.MERGE_INTO,
    duckdb.StatementType.CREATE,
    duckdb.StatementType.ALTER,
    duckdb.StatementType.DROP,
}

# Derived rather than listed again, so a statement type admitted in future is
# gated as a write by default instead of silently becoming a permitted read.
_WRITE_TYPES = _ALLOWED_TYPES - {duckdb.StatementType.SELECT}


class SQLNotAllowed(ValueError):
    """Raised when `assert_allowed` rejects the SQL.

    Carries a short, user-facing detail in `args[0]`; routers should
    re-raise as HTTP 422 with `{"error": "sql_not_allowed", "detail": ...}`.
    """


def assert_allowed(sql: str) -> None:
    try:
        statements = duckdb.extract_statements(sql)
    except Exception as exc:  # ParserException et al
        raise SQLNotAllowed(f"SQL parse error: {exc}") from exc

    if not statements:
        raise SQLNotAllowed("Empty SQL")

    disallowed = [s.type.name for s in statements if s.type not in _ALLOWED_TYPES]
    if disallowed:
        names = ", ".join(sorted(set(disallowed)))
        raise SQLNotAllowed(
            f"Disallowed statement type(s): {names}. Allowed: SELECT, INSERT, "
            "UPDATE, DELETE, MERGE, CREATE, ALTER, DROP."
        )


def is_read_only(sql: str) -> bool:
    """True iff every statement is a ``SELECT`` (no writes/DDL).

    Used by the catalog-migration freeze gate to keep reads flowing while a
    catalog is read-only mid-migration. Reuses the same lexical parse as
    ``assert_allowed`` — no execution. Unparseable SQL is treated as not
    read-only so the stricter ``assert_allowed`` path reports the parse error."""
    try:
        statements = duckdb.extract_statements(sql)
    except Exception:  # noqa: BLE001 - parse failure -> not provably read-only
        return False
    return bool(statements) and all(s.type == duckdb.StatementType.SELECT for s in statements)


def is_write(sql: str) -> bool:
    """True iff some statement writes data or changes the catalog.

    Deliberately **not** the negation of :func:`is_read_only`. Three things are
    neither: SQL that does not parse, an empty body, and a statement this
    platform refuses outright (``EXPLAIN``, ``ATTACH``, ``PRAGMA``, …). Each is
    rejected for a reason that has nothing to do with write permission, so a
    caller gating on writes must let them through to ``assert_allowed`` and
    report *its* message instead.

    Answering "is this a write?" with "yes" for a typo would send someone off
    to grant write access, or an operator off to change a setting, when the fix
    is a missing letter.
    """
    try:
        statements = duckdb.extract_statements(sql)
    except Exception:  # noqa: BLE001 - unparseable is not provably a write
        return False
    return any(s.type in _WRITE_TYPES for s in statements)
