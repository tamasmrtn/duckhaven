"""SQL text helpers shared by the control plane and the agent."""

from __future__ import annotations


def quote_ident(name: str) -> str:
    """Quote ``name`` as one DuckDB identifier, doubling any embedded double quote.

    Every identifier interpolated into SQL that DuckHaven builds itself goes
    through this, so a catalog, schema or table name is always read as a name.
    """
    return '"' + name.replace('"', '""') + '"'
