"""Classify a statement into the coarse kind History filters on.

Persisted on :class:`~api.models.query.Query` at insert time rather than derived
when History is read: a derived value cannot be filtered in SQL, cannot be
indexed, and would re-parse every row on every page.

The taxonomy is deliberately coarse — it answers "what kind of thing did this
run do", not "what exactly did it do". It is drawn from the statement node types
:mod:`api.services.statement_policy` already admits, so it covers exactly the
surface DuckHaven accepts and nothing speculative.

This is *not* the agent's classification. The agent types statements with
DuckDB's own ``extract_statements`` for admission decisions, and the two
disagree by design: DuckDB reports ``SHOW``/``DESCRIBE``/``SUMMARIZE`` as
``SELECT`` and ``ANALYZE`` as ``VACUUM`` (see ``agent/executor/runner.py``).
Reconciling them would make both worse. History filters on this one; admission
keeps using DuckDB's.

Like :mod:`api.services.lineage.extract` and unlike the grant/policy paths, this
fails *open*: an unparseable statement is classified ``None`` (unknown), never
guessed at. ``None`` and ``"other"`` mean different things and must not be
conflated — ``other`` is "parsed fine, nothing more specific fits", ``None`` is
"we do not know", which is also what every row written before this column
existed carries.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

# Node type -> statement kind. Ordered most-specific first where sqlglot's
# hierarchy overlaps; matched with isinstance so subclasses land on their base.
_NODE_TYPES: tuple[tuple[type[exp.Expression], str], ...] = (
    # Reads. Union/Subquery are how sqlglot spells `a UNION b` and `(SELECT 1)`.
    # Summarize and Pragma return a result grid, so they read like a SELECT to
    # anyone scanning History for "queries I ran".
    (exp.Select, "select"),
    (exp.Union, "select"),
    (exp.Subquery, "select"),
    (exp.Summarize, "select"),
    (exp.Pragma, "select"),
    # Writes.
    (exp.Insert, "insert"),
    (exp.Update, "update"),
    # DuckDB's TRUNCATE builds the same node family as DELETE and shares its
    # plan, so it is grouped with it rather than given a kind of its own.
    (exp.TruncateTable, "delete"),
    (exp.Delete, "delete"),
    (exp.Merge, "merge"),
    (exp.Copy, "copy"),
    # DDL.
    (exp.Create, "create"),
    (exp.Alter, "alter"),
    (exp.Drop, "drop"),
    # Introspection. SHOW and DESCRIBE answer the same question, so one kind.
    (exp.Describe, "describe"),
    (exp.Show, "describe"),
)

# Every kind this module can return, for validating a filter value before it
# reaches SQL. "other" is included; None is not a filterable value.
STATEMENT_TYPES: frozenset[str] = frozenset(kind for _, kind in _NODE_TYPES) | {"other"}


def classify_statement(sql: str) -> str | None:
    """The kind of statement ``sql`` is, or ``None`` when it cannot be parsed.

    A multi-statement script is classified by its **first** statement. Scripts
    are rare in History (they arrive through SQL sessions, one row per
    statement), and picking the first is predictable in a way that "the most
    interesting one" is not.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - fail open: unknown beats a wrong guess
        return None

    first = next((s for s in statements if s is not None), None)
    if first is None:
        return None

    # sqlglot does not raise on syntax it cannot handle: it falls back to lexing
    # the statement as a raw Command. That is the parser saying "I do not know",
    # which is `None`, not `other` — EXPLAIN, CALL and VACUUM land here too, and
    # calling them "other" would be a guess dressed up as a classification.
    if isinstance(first, exp.Command):
        return None

    for node_type, kind in _NODE_TYPES:
        if isinstance(first, node_type):
            return kind
    # Parsed into a known node, but nothing more specific fits: USE, SET,
    # transaction control, ATTACH, ANALYZE.
    return "other"
