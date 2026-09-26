"""Shared plan/profile tree-walker: canned JSON + a real-DuckDB round-trip."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from agent.executor.plan import parse_explain, parse_profile
from agent.executor.runner import run_query_sync

# A trimmed EXPLAIN (FORMAT json) physical_plan (the real DuckDB shape).
CANNED_EXPLAIN = [
    {
        "name": "HASH_GROUP_BY",
        "extra_info": {"Groups": "#0", "Aggregates": ["count_star()"]},
        "children": [
            {
                "name": "SEQ_SCAN",
                "extra_info": {"Table": "memory.main.t", "Estimated Cardinality": "1000"},
                "children": [],
            }
        ],
    }
]

# A trimmed profile (QUERY_ROOT + one operator child).
CANNED_PROFILE = {
    "latency": 0.5,
    "cpu_time": 1.25,
    "rows_returned": 30,
    "result_set_size": 4096,
    "system_peak_buffer_memory": 268435456,
    "system_peak_temp_dir_size": 79495168,
    "total_bytes_read": 1024,
    "total_bytes_written": 0,
    "children": [
        {
            "operator_type": "ORDER_BY",
            "operator_name": "ORDER_BY",
            "operator_cardinality": 30,
            "operator_rows_scanned": 0,
            "operator_timing": 0.3,
            "result_set_size": 2048,
            "extra_info": {"Order By": "c DESC", "Estimated Cardinality": "100"},
            "children": [],
        }
    ],
}


# The same statement's profile as DuckDB 2.0 writes it (trimmed from a real
# v2.0.0-alpha43385 capture of the materialize path): metrics grouped under
# query/system/io, operator fields renamed, and the plan under an extra
# RESULT_COLLECTOR 1.5 never reported.
CANNED_PROFILE_2_0 = {
    "io": {"total_bytes_read": 1024, "total_bytes_written": 0},
    "query": {"cpu_time": 1.25, "sql": "", "total_rows_scanned": 50000, "total_time": 0.5},
    "system": {
        "blocked_thread_time": 0.25,
        "peak_buffer_memory": 268435456,
        "peak_temp_dir_size": 79495168,
        "total_memory_allocated": 1310720,
    },
    "operator": [
        {
            "type": "RESULT_COLLECTOR",
            "intermediate_rows": 0,
            "intermediate_size_bytes": 0,
            "timing": 3.9e-06,
            "extra_info": {},
            "children": [
                {
                    "type": "ORDER_BY",
                    "intermediate_rows": 30,
                    "intermediate_size_bytes": 4096,
                    "timing": 0.3,
                    "extra_info": {"Order By": "c DESC", "Estimated Cardinality": "100"},
                    "children": [
                        {
                            "type": "TABLE_SCAN",
                            "intermediate_rows": 50000,
                            "intermediate_size_bytes": 400000,
                            "rows_scanned": 50000,
                            "timing": 0.1,
                            "extra_info": {},
                        }
                    ],
                }
            ],
        }
    ],
}


def test_parse_explain_reads_type_and_estimated_cardinality():
    tree = parse_explain(CANNED_EXPLAIN)
    assert tree.type == "HASH_GROUP_BY"
    assert tree.estimated_cardinality is None  # group-by carries no EC of its own
    assert tree.children[0].type == "SEQ_SCAN"
    assert tree.children[0].estimated_cardinality == 1000


def test_parse_profile_summary_and_units():
    summary, tree = parse_profile(CANNED_PROFILE)
    assert summary.latency_ms == 500.0  # seconds -> ms
    assert summary.cpu_time_ms == 1250.0
    assert summary.rows_returned == 30
    assert summary.peak_memory_bytes == 268435456
    assert summary.spill_bytes == 79495168
    # The QUERY_ROOT's single child is the plan root.
    assert tree.type == "ORDER_BY"
    assert tree.rows_produced == 30
    assert tree.time_ms == 300.0
    assert tree.estimated_cardinality == 100


def test_real_duckdb_explain_and_profile_round_trip(tmp_path: Path):
    """A real DuckDB EXPLAIN + profile so format drift breaks this test."""
    conn = duckdb.connect()
    conn.execute("CREATE TABLE t AS SELECT i id, i % 100 g FROM range(50000) tbl(i)")

    plan_rows = conn.execute(
        "EXPLAIN (FORMAT json) SELECT g, count(*) c FROM t GROUP BY g ORDER BY c DESC"
    ).fetchall()
    tree = parse_explain(json.loads(plan_rows[0][1]))
    types = []
    stack = [tree]
    while stack:
        node = stack.pop()
        types.append(node.type)
        stack.extend(node.children)
    assert any("GROUP_BY" in t for t in types)
    assert "ORDER_BY" in types

    out = tmp_path / "r.parquet"
    stats = run_query_sync(
        "SELECT g, count(*) c FROM t GROUP BY g ORDER BY c DESC",
        out,
        memory_bytes=2 * 1024**3,
        threads=2,
        conn=conn,
        enable_profiling=True,
    )
    profile = stats["profile"]
    assert profile is not None
    assert profile["summary"]["latency_ms"] > 0
    # The plan is COPY-wrapped (we materialize to Parquet), so query-level
    # rows_returned is the COPY count; the group-by's 100 groups show in the
    # operator tree's actual cardinalities.
    produced = []
    stack = [profile["tree"]]
    while stack:
        node = stack.pop()
        if node["rows_produced"] is not None:
            produced.append(node["rows_produced"])
        stack.extend(node["children"])
    assert 100 in produced

    # Present on a real capture, not just in the canned fixture: this is the
    # assertion that breaks if DuckDB stops reporting the metric or renames it.
    assert "blocked_thread_time_ms" in profile["summary"]
    assert profile["summary"]["blocked_thread_time_ms"] >= 0.0
    # Injected by the runner rather than parsed, and likewise absent from
    # QueryProfileSummary until now.
    assert "admission_wait_ms" in profile["summary"]


def test_blocked_thread_time_is_carried_into_the_summary():
    """DuckDB reports it on every profiled run; nothing used to read it.

    Seconds on the wire like ``latency`` and ``cpu_time``, so it gets the same
    conversion.
    """
    summary, _ = parse_profile({**CANNED_PROFILE, "blocked_thread_time": 0.25})
    assert summary.blocked_thread_time_ms == 250.0
    assert summary.to_dict()["blocked_thread_time_ms"] == 250.0


def test_blocked_thread_time_defaults_to_zero_when_absent():
    """A profile captured before the metric was requested still parses."""
    summary, _ = parse_profile(CANNED_PROFILE)
    assert summary.blocked_thread_time_ms == 0.0


def test_duckdb_2_profile_parses_to_the_1_5_shape():
    """A profile from an agent on either DuckDB line reads the same downstream."""
    summary, tree = parse_profile(CANNED_PROFILE_2_0)
    assert summary.latency_ms == 500.0
    assert summary.cpu_time_ms == 1250.0
    # Taken from the top operator, as 1.5 does, not from the collector wrapper.
    assert summary.rows_returned == 30
    assert summary.result_bytes == 4096
    assert summary.peak_memory_bytes == 268435456
    assert summary.spill_bytes == 79495168
    assert summary.memory_allocated_bytes == 1310720
    assert summary.bytes_read == 1024
    assert summary.blocked_thread_time_ms == 250.0
    assert tree.type == "ORDER_BY"
    assert tree.rows_produced == 30
    assert tree.time_ms == 300.0
    assert tree.estimated_cardinality == 100
    scan = tree.children[0]
    assert scan.type == "TABLE_SCAN"
    assert scan.rows_scanned == 50000


def test_both_profile_shapes_yield_the_same_summary():
    one_five, _ = parse_profile(CANNED_PROFILE)
    two_zero, _ = parse_profile(CANNED_PROFILE_2_0)
    for field in ("latency_ms", "cpu_time_ms", "rows_returned", "peak_memory_bytes", "spill_bytes"):
        assert getattr(one_five, field) == getattr(two_zero, field), field
