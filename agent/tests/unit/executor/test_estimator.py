"""EXPLAIN-based cost estimation: memory estimate, bucket mapping, unestimable input."""

from __future__ import annotations

import duckdb
import pytest

from agent.executor.estimator import bucket_for, estimate_memory_bytes
from duckhaven_shared.concurrency import BUCKET_FRACTIONS

BUDGET = 12 * 1024**3  # 12 GiB makes the bucket fractions land on clean sizes


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TABLE t AS SELECT i id, i % 100 g, 'x' || i s FROM range(200000) tbl(i)")
    c.execute("CREATE TABLE u AS SELECT i id, i * 2 v FROM range(100000) tbl(i)")
    yield c
    c.close()


def test_group_by_has_positive_estimate(conn):
    est = estimate_memory_bytes(conn, "SELECT g, count(*) FROM t GROUP BY g", safety=1.5)
    assert est is not None and est > 0


def test_join_has_positive_estimate(conn):
    est = estimate_memory_bytes(
        conn, "SELECT g, sum(v) FROM t JOIN u USING(id) GROUP BY g", safety=1.5
    )
    assert est is not None and est > 0


def test_streaming_filter_estimates_zero(conn):
    # No blocking operator -> 0 bytes (a valid cheap estimate, not None).
    est = estimate_memory_bytes(conn, "SELECT * FROM t WHERE id > 5", safety=1.5)
    assert est == 0


def test_order_by_is_blocking(conn):
    est = estimate_memory_bytes(conn, "SELECT * FROM t ORDER BY s", safety=1.5)
    assert est is not None and est > 0


def test_window_is_blocking(conn):
    est = estimate_memory_bytes(
        conn, "SELECT id, row_number() OVER (ORDER BY id) FROM t", safety=1.5
    )
    assert est is not None and est > 0


def test_safety_multiplier_scales_estimate(conn):
    sql = "SELECT id, count(*) FROM t GROUP BY id"
    low = estimate_memory_bytes(conn, sql, safety=1.0)
    high = estimate_memory_bytes(conn, sql, safety=3.0)
    assert low is not None and high is not None
    assert high == pytest.approx(low * 3, rel=0.01)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE z (a int)",  # DDL
        "INSERT INTO t VALUES (1, 1, 'a')",  # DML
        "SELECT 1; SELECT 2",  # multi-statement
        "SELECT * FROM does_not_exist",  # unbindable
    ],
)
def test_unestimable_returns_none(conn, sql):
    assert estimate_memory_bytes(conn, sql, safety=1.5) is None


def test_bucket_for_snaps_up():
    # An estimate just over the S bucket snaps to M.
    s_bytes = int(BUCKET_FRACTIONS["S"] * BUDGET)
    mem, frac, label = bucket_for(s_bytes + 1, BUDGET, BUCKET_FRACTIONS)
    assert label == "M"
    assert frac == BUCKET_FRACTIONS["M"]
    assert mem == int(BUCKET_FRACTIONS["M"] * BUDGET)


def test_bucket_for_zero_estimate_is_smallest():
    _, frac, label = bucket_for(0, BUDGET, BUCKET_FRACTIONS)
    assert label == "XS"
    assert frac == BUCKET_FRACTIONS["XS"]


def test_bucket_for_huge_estimate_caps_at_xl():
    _, frac, label = bucket_for(BUDGET * 100, BUDGET, BUCKET_FRACTIONS)
    assert label == "XL"
    assert frac == 1.0


def test_no_bucket_below_the_top_serializes_the_agent():
    """Every rung except the largest must admit at least two statements.

    The ladder decides concurrency, not just memory: a rung at 2/3 of the budget
    admits exactly one statement, which is what XL already means, so it costs two
    thirds of the agent and buys nothing. It also puts a cliff directly above M —
    an estimate one byte past M went from 3-way concurrency to 1-way, and on a
    22-way SF10 burst that serialized every statement behind it.
    """
    ladder = sorted(BUCKET_FRACTIONS.items(), key=lambda kv: kv[1])
    for label, frac in ladder[:-1]:
        assert int(1 // frac) >= 2, f"bucket {label} ({frac}) admits only one statement"
    # And no step may cost more than half the concurrency of the one below it.
    concurrency = [int(1 // frac) for _, frac in ladder]
    for below, above in zip(concurrency[1:], concurrency[:-1], strict=True):
        assert below * 2 >= above, f"concurrency falls {above} -> {below} in one step"
