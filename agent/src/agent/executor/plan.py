"""Shared DuckDB plan/profile tree-walker.

DuckDB reports a query's plan as a tree of nested nodes both *before* execution
(``EXPLAIN (FORMAT json)``, with estimated cardinalities) and *after* execution
(the JSON profile, with actual per-operator metrics). The two share the same
nested ``children`` shape but use different key names, so this module provides
ONE recursive walker (:func:`_walk`) and two field extractors:

- :func:`parse_explain` builds an estimate-only tree (operator type + ``EC``)
  consumed by the cost estimator.
- :func:`parse_profile` builds an actuals tree (rows/bytes/time) plus a
  query-level summary, consumed by the post-execution profile UI.

Do not duplicate traversal: both estimator and profiler depend on this.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedNode:
    """A single operator in the normalized plan/profile tree.

    Estimate-only fields (rows_scanned/rows_produced/time_ms/result_bytes) are
    ``None`` for trees built from ``EXPLAIN``; ``estimated_cardinality`` is
    populated for both (it lives in ``extra_info`` of either format).
    """

    type: str
    name: str
    estimated_cardinality: int | None = None
    rows_scanned: int | None = None
    rows_produced: int | None = None
    time_ms: float | None = None
    result_bytes: int | None = None
    extra_info: dict[str, Any] = field(default_factory=dict)
    children: list[NormalizedNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the wire shape sent in ``QUERY_DONE`` and stored as JSON."""
        return {
            "type": self.type,
            "name": self.name,
            "estimated_cardinality": self.estimated_cardinality,
            "rows_scanned": self.rows_scanned,
            "rows_produced": self.rows_produced,
            "time_ms": self.time_ms,
            "result_bytes": self.result_bytes,
            "extra_info": self.extra_info,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class QuerySummary:
    """Query-level actuals from the profile root (QUERY_ROOT)."""

    latency_ms: float
    cpu_time_ms: float
    rows_returned: int
    result_bytes: int
    peak_memory_bytes: int
    spill_bytes: int
    bytes_read: int
    bytes_written: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "latency_ms": self.latency_ms,
            "cpu_time_ms": self.cpu_time_ms,
            "rows_returned": self.rows_returned,
            "result_bytes": self.result_bytes,
            "peak_memory_bytes": self.peak_memory_bytes,
            "spill_bytes": self.spill_bytes,
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
        }


def _parse_ec(extra_info: dict[str, Any]) -> int | None:
    """``extra_info["Estimated Cardinality"]`` arrives as a string; coerce it."""
    raw = (extra_info or {}).get("Estimated Cardinality")
    if raw is None:
        return None
    try:
        return int(raw)
    except TypeError, ValueError:
        return None


def _walk(
    raw: dict[str, Any], fields: Callable[[dict[str, Any]], NormalizedNode]
) -> NormalizedNode:
    """Recurse the ``children`` array, building a node via the ``fields`` extractor."""
    node = fields(raw)
    node.children = [_walk(child, fields) for child in raw.get("children", []) or []]
    return node


def _explain_fields(raw: dict[str, Any]) -> NormalizedNode:
    extra = raw.get("extra_info", {}) or {}
    name = raw.get("name", "")
    return NormalizedNode(
        type=name,
        name=name,
        estimated_cardinality=_parse_ec(extra),
        extra_info=extra,
    )


def _profile_fields(raw: dict[str, Any]) -> NormalizedNode:
    extra = raw.get("extra_info", {}) or {}
    timing = raw.get("operator_timing")
    return NormalizedNode(
        type=raw.get("operator_type", ""),
        name=raw.get("operator_name", "") or raw.get("operator_type", ""),
        estimated_cardinality=_parse_ec(extra),
        rows_scanned=raw.get("operator_rows_scanned"),
        rows_produced=raw.get("operator_cardinality"),
        time_ms=(timing * 1000.0) if isinstance(timing, (int, float)) else None,
        result_bytes=raw.get("result_set_size"),
        extra_info=extra,
    )


def parse_explain(physical_plan: list[dict[str, Any]] | dict[str, Any]) -> NormalizedNode:
    """Parse ``EXPLAIN (FORMAT json)`` output (a 1-element array) into a tree."""
    root = physical_plan[0] if isinstance(physical_plan, list) else physical_plan
    return _walk(root, _explain_fields)


def parse_profile(profile: dict[str, Any]) -> tuple[QuerySummary, NormalizedNode]:
    """Parse the DuckDB JSON profile into a query summary + operator tree.

    The profile root (QUERY_ROOT) carries the query-level metrics and has no
    ``operator_type``; the executed plan begins at its single child.
    """

    def _num(key: str) -> float:
        val = profile.get(key)
        return val if isinstance(val, (int, float)) else 0

    summary = QuerySummary(
        latency_ms=_num("latency") * 1000.0,
        cpu_time_ms=_num("cpu_time") * 1000.0,
        rows_returned=int(_num("rows_returned")),
        result_bytes=int(_num("result_set_size")),
        peak_memory_bytes=int(_num("system_peak_buffer_memory")),
        spill_bytes=int(_num("system_peak_temp_dir_size")),
        bytes_read=int(_num("total_bytes_read")),
        bytes_written=int(_num("total_bytes_written")),
    )
    children = profile.get("children", []) or []
    # The real plan root is the QUERY_ROOT's single child; fall back to the root
    # itself if the profile is already operator-shaped.
    plan_root = children[0] if children else profile
    tree = _walk(plan_root, _profile_fields)
    return summary, tree
