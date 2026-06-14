"""Tier-2 query cost estimation.

Sizes a query's memory reservation from DuckDB's optimizer estimate (``EXPLAIN``)
instead of a fixed slot. Peak memory is approximated as the sum, over *blocking*
operators (those that buffer their input), of ``estimated_cardinality *
row_width``; streaming operators contribute ~0. The estimate then snaps to a
"T-shirt" bucket of the agent's budget (see :func:`bucket_for`).

Estimation is best-effort: any failure (DDL/DML, multi-statement, unbindable SQL,
EXPLAIN/DESCRIBE error) returns ``None`` so the caller falls back to a default
reservation. It never raises to the dispatch path.
"""

from __future__ import annotations

import json
import logging

import duckdb

from agent.executor.plan import NormalizedNode, parse_explain
from agent.executor.runner import _is_single_select

logger = logging.getLogger(__name__)

# Operators that buffer (a large fraction of) their input in memory. Everything
# else streams and is treated as ~0 incremental memory. Names match DuckDB's
# physical operator types in ``EXPLAIN (FORMAT json)``.
BLOCKING_OPERATORS: frozenset[str] = frozenset(
    {
        "HASH_JOIN",
        "HASH_GROUP_BY",
        "PERFECT_HASH_GROUP_BY",
        "UNGROUPED_AGGREGATE",
        "ORDER_BY",
        "WINDOW",
        "NESTED_LOOP_JOIN",
        "PIECEWISE_MERGE_JOIN",
        "IE_JOIN",
        "DISTINCT",
    }
)

# Approximate in-memory byte width per DuckDB output column type. Strings/blobs
# are estimated as a pointer-sized handle (DuckDB stores them out-of-line); this
# is a deliberate, documented approximation smoothed over by the safety factor.
TYPE_BYTES: dict[str, int] = {
    "BOOLEAN": 1,
    "TINYINT": 1,
    "UTINYINT": 1,
    "SMALLINT": 2,
    "USMALLINT": 2,
    "INTEGER": 4,
    "UINTEGER": 4,
    "FLOAT": 4,
    "DATE": 4,
    "TIME": 8,
    "BIGINT": 8,
    "UBIGINT": 8,
    "DOUBLE": 8,
    "TIMESTAMP": 8,
    "TIMESTAMP WITH TIME ZONE": 8,
    "HUGEINT": 16,
    "UHUGEINT": 16,
    "UUID": 16,
    "VARCHAR": 16,
    "BLOB": 16,
    "DECIMAL": 8,
}
_DEFAULT_TYPE_BYTES = 16


def _type_bytes(column_type: str) -> int:
    base = column_type.upper().split("(", 1)[0].strip()
    return TYPE_BYTES.get(base, _DEFAULT_TYPE_BYTES)


def _row_width(conn: duckdb.DuckDBPyConnection, sql: str, default: int) -> int:
    """Output row width from ``DESCRIBE`` (binds, does not execute the query)."""
    try:
        rows = conn.execute(f"DESCRIBE {sql}").fetchall()
    except Exception:  # noqa: BLE001 - fall back to a flat width
        return default
    width = sum(_type_bytes(str(r[1])) for r in rows)
    return width or default


def _effective_card(node: NormalizedNode) -> int:
    """Cardinality to charge a blocking operator.

    Some blocking ops carry no EC of their own (e.g. ``PERFECT_HASH_GROUP_BY``);
    fall back to the largest child EC, since the operator buffers its input.
    """
    if node.estimated_cardinality is not None:
        return node.estimated_cardinality
    child_ecs = [c.estimated_cardinality for c in node.children if c.estimated_cardinality]
    return max(child_ecs) if child_ecs else 0


def estimate_memory_bytes(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    safety: float = 1.5,
    default_row_width: int = 64,
) -> int | None:
    """Estimate peak memory for ``sql`` on an already-attached connection.

    Returns ``None`` when unestimable (not a single SELECT, or EXPLAIN raises).
    A pure streaming query returns 0 (no blocking operators) — a valid, cheap
    estimate, distinct from ``None``.
    """
    if not _is_single_select(sql):
        return None
    try:
        plan_rows = conn.execute(f"EXPLAIN (FORMAT json) {sql}").fetchall()
        physical_plan = json.loads(plan_rows[0][1])
    except Exception as exc:  # noqa: BLE001 - estimation is best-effort
        logger.info("EXPLAIN estimate failed: %s", exc)
        return None

    row_width = _row_width(conn, sql, default_row_width)
    tree = parse_explain(physical_plan)

    peak = 0
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.type in BLOCKING_OPERATORS:
            peak += _effective_card(node) * row_width
        stack.extend(node.children)
    return int(peak * safety)


def bucket_for(
    estimate_bytes: int,
    budget: int,
    fractions: dict[str, float],
) -> tuple[int, float, str]:
    """Snap an estimate up to the smallest budget-fraction bucket that fits.

    Returns ``(memory_bytes, fraction, label)``. Buckets are evaluated ascending
    by fraction; an estimate above every bucket lands in the largest one.
    """
    target = max(0, estimate_bytes)
    for label, frac in sorted(fractions.items(), key=lambda kv: kv[1]):
        if frac * budget >= target:
            return int(frac * budget), frac, label
    label, frac = max(fractions.items(), key=lambda kv: kv[1])
    return int(frac * budget), frac, label
