"""One sqlglot parse of a statement, shared by every check that reads it.

A submitted statement is parsed by the denylist, then again when its type is
classified, and in a scoped workspace three more times by the grant checks:
about 10 ms each for a 6 KB dbt model, all on the event loop. The checks run
within milliseconds of each other, so a small cache keyed on the text serves
all of them from the first parse.

The trees are shared, so a caller must only read them, never modify one;
``tests/unit/services/test_sql_parse.py`` holds every caller to that. A parse
that raises is not cached, and raises again for the next caller.
"""

from __future__ import annotations

import functools

import sqlglot
from sqlglot import exp


@functools.lru_cache(maxsize=16)
def parse_duckdb(sql: str) -> tuple[exp.Expression | None, ...]:
    """``sqlglot.parse(sql, read="duckdb")``, shared; read the result, never modify it."""
    return tuple(sqlglot.parse(sql, read="duckdb"))
