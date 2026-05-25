"""SQL allowlist enforced before dispatch (G-D8-a).

Only `SELECT` and `INSERT` (incl. `INSERT … SELECT` and CTE-wrapped
INSERT) are permitted; every other DuckDB statement type is rejected.
Multi-statement bodies must consist entirely of allowed statements.

The control plane uses DuckDB *as a parser*: `duckdb.extract_statements`
is purely lexical/syntactic — no execution, no storage, no extensions
loaded. D1's intent ("control plane does not run DuckDB") is preserved
because we never construct a connection, never bind to a database, and
never call `.execute()` on user SQL.
"""

from __future__ import annotations

import duckdb

_ALLOWED_TYPES = {duckdb.StatementType.SELECT, duckdb.StatementType.INSERT}


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
            f"Disallowed statement type(s): {names}. Only SELECT and INSERT are permitted."
        )
