import time

import pytest

from agent.executor.runner import _result_schema, run_query_sync
from agent.executor.supervisor import run_query

# The admission manager normally supplies per-query sizing; tests that don't
# exercise sizing pass a fixed slice via this helper.
_MEM = 1024**3
_THREADS = 2


def _run(sql, result_path, **kwargs):
    kwargs.setdefault("memory_bytes", _MEM)
    kwargs.setdefault("threads", _THREADS)
    return run_query_sync(sql, result_path, **kwargs)


def test_simple_select_produces_parquet(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT 42 AS answer", result_path)
    assert result_path.exists()
    assert stats["row_count"] == 1
    assert stats["wrote_result"] is True
    assert stats["duration_ms"] >= 0


def test_select_reports_result_bytes(tmp_path):
    """A materialized SELECT reports the Parquet result file's size."""
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT * FROM range(100) t(x)", result_path)
    assert stats["result_bytes"] == result_path.stat().st_size
    assert stats["result_bytes"] > 0


def test_ddl_runs_without_result_file(tmp_path):
    """Pure DDL executes but writes no Parquet and reports zero rows."""
    result_path = tmp_path / "out.parquet"
    stats = _run("CREATE TABLE t (x INT)", result_path)
    assert not result_path.exists()
    assert stats["wrote_result"] is False
    assert stats["row_count"] == 0
    assert stats["result_bytes"] is None
    assert stats["result_schema"] is None


def test_result_schema_reports_duckdb_type_spellings(tmp_path):
    """The reported types are DuckDB's own logical-type spellings, including
    parameterized and nested ones (the same strings DESCRIBE prints)."""
    result_path = tmp_path / "out.parquet"
    stats = _run(
        """
        SELECT
          TIMESTAMPTZ '2024-03-01 12:00:00+01' AS ts_tz,
          123.4567::DECIMAL(38,10) AS dec,
          'abc'::BLOB AS b,
          [1, 2, 3] AS lst,
          {'a': 1, 'b': 'x'} AS st,
          MAP {'k': 1} AS mp,
          NULL::INTEGER AS n
        """,
        result_path,
    )
    assert stats["result_schema"] == [
        {"name": "ts_tz", "type": "TIMESTAMP WITH TIME ZONE"},
        {"name": "dec", "type": "DECIMAL(38,10)"},
        {"name": "b", "type": "BLOB"},
        {"name": "lst", "type": "INTEGER[]"},
        {"name": "st", "type": "STRUCT(a INTEGER, b VARCHAR)"},
        {"name": "mp", "type": "MAP(VARCHAR, INTEGER)"},
        {"name": "n", "type": "INTEGER"},
    ]


def test_result_schema_survives_the_lossy_parquet_write(tmp_path):
    """The types come off the relation, not the Parquet file.

    DuckDB's Parquet writer degrades these four (HUGEINT loses its values too),
    so a schema derived from the materialized file would report the wrong type.
    """
    result_path = tmp_path / "out.parquet"
    stats = _run(
        "SELECT 123456789012345678901::HUGEINT AS h, 'e'::ENUM('e', 'f') AS en, "
        "[1, 2]::INT[2] AS arr, bitstring('0101', 4) AS bs",
        result_path,
    )
    assert stats["result_schema"] == [
        {"name": "h", "type": "HUGEINT"},
        {"name": "en", "type": "ENUM('e', 'f')"},
        {"name": "arr", "type": "INTEGER[2]"},
        {"name": "bs", "type": "BIT"},
    ]

    import duckdb

    conn = duckdb.connect()
    try:
        readback = conn.sql(f"SELECT * FROM read_parquet('{result_path}')")
        # What the API would have derived instead, had it read the file.
        assert [str(t) for t in readback.types] == ["DOUBLE", "VARCHAR", "INTEGER[]", "VARCHAR"]
    finally:
        conn.close()


def test_result_schema_capture_failure_degrades_to_none():
    """A capture failure yields no schema rather than failing the query."""

    class Broken:
        columns = ["a"]

        @property
        def types(self):
            raise RuntimeError("relation gone")

    assert _result_schema(Broken()) is None


def test_dml_reports_affected_count(tmp_path):
    """A multi-statement DDL+DML script runs directly and reports the affected
    row count from the final statement (no result file)."""
    result_path = tmp_path / "out.parquet"
    stats = _run(
        "CREATE TABLE t (x INT); INSERT INTO t VALUES (1), (2), (3)",
        result_path,
    )
    assert not result_path.exists()
    assert stats["wrote_result"] is False
    assert stats["row_count"] == 3


def test_multiple_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT unnest([1,2,3]) AS n", result_path)
    assert stats["row_count"] == 3


@pytest.mark.parametrize(("mem_bytes", "threads"), [(4 * 1024**3, 3), (16 * 1024**3, 8)])
def test_duckdb_pinned_to_granted_slice(tmp_path, mem_bytes, threads):
    """Every session pins DuckDB memory_limit AND threads to the slice the
    admission manager granted -- never a static default, and never DuckDB's
    cgroup-blind default thread count."""
    result_path = tmp_path / "out.parquet"
    run_query_sync(
        "SELECT current_setting('memory_limit') AS m, current_setting('threads') AS t",
        result_path,
        memory_bytes=mem_bytes,
        threads=threads,
    )
    import duckdb

    row = duckdb.connect().execute(f"SELECT * FROM read_parquet('{result_path}')").fetchone()
    mem_setting, threads_setting = row
    assert int(threads_setting) == threads
    # Asserted against the granted bytes directly, NOT by round-tripping the same
    # expression the runner uses: that only proves the runner agrees with itself.
    # It did, while handing DuckDB a `GB` suffix for a value computed in GiB --
    # DuckDB reads GB as 10**9, so every slot was ~7% smaller than granted.
    # The parametrized sizes are whole GiB, which DuckDB displays exactly.
    assert mem_setting == f"{mem_bytes // 1024**3}.0 GiB"


async def test_timeout_interrupts_running_query(tmp_path):
    """A wall-clock timeout interrupts the in-flight DuckDB query (G-D2-a):
    the call raises TimeoutError far sooner than the query would complete on
    its own, proving the interrupt stopped real work rather than just the
    awaiting coroutine."""
    result_path = tmp_path / "out.parquet"
    # A 10^12-row cross join with a per-row computation: minutes of work if it
    # ran to completion, so finishing under the budget can only mean interrupt.
    sql = "SELECT sum(t1.range + t2.range) FROM range(1000000) t1, range(1000000) t2"

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await run_query(sql, result_path, timeout_s=0.5, memory_bytes=_MEM, threads=_THREADS)
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"interrupt did not stop the query promptly ({elapsed:.1f}s)"


def test_invalid_sql_raises(tmp_path):
    result_path = tmp_path / "out.parquet"
    with pytest.raises(Exception):
        _run("THIS IS NOT VALID SQL !!!", result_path)


def test_empty_result_produces_zero_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT 1 WHERE 1=0", result_path)
    assert result_path.exists()
    assert stats["row_count"] == 0


# DuckDB reports all of these as StatementType.SELECT, so they reach the
# materialize path -- but none of them is a legal source for `COPY (...) TO`,
# which accepts only a table name or a query. Materializing them by string-
# building a COPY therefore failed with a ParserException instead of returning
# rows; `DESCRIBE` broke `dbt snapshot` outright. Wrapping the body in
# `SELECT * FROM (...)` rescues the first three but NOT `PRAGMA`, which is why
# the runner materializes through the relational API instead.
@pytest.mark.parametrize(
    ("sql", "expected_rows"),
    [
        ("DESCRIBE (SELECT 1 AS a, 2 AS b)", 2),
        ("DESCRIBE SELECT 1 AS a", 1),
        ("SHOW DATABASES", 1),
        ("SUMMARIZE SELECT 1 AS a", 1),
        ("PRAGMA version", 1),
        ("PRAGMA database_list", 1),
    ],
)
def test_select_shaped_meta_statements_materialize(tmp_path, sql, expected_rows):
    """Statements DuckDB types as SELECT return a result grid, not a parser error."""
    result_path = tmp_path / "out.parquet"
    stats = _run(sql, result_path)
    assert result_path.exists()
    assert stats["wrote_result"] is True
    assert stats["row_count"] == expected_rows


def test_describe_result_carries_the_column_metadata(tmp_path):
    """The materialized DESCRIBE holds DuckDB's real describe columns -- the
    metadata dbt/dlt read for column schema and schema evolution."""
    import duckdb

    result_path = tmp_path / "out.parquet"
    _run("DESCRIBE (SELECT 1 AS a, 'x' AS b)", result_path)
    rows = duckdb.connect().execute(
        f"SELECT column_name, column_type FROM read_parquet('{result_path}') ORDER BY column_name"
    )
    assert rows.fetchall() == [("a", "INTEGER"), ("b", "VARCHAR")]


def test_select_profile_reports_true_result_row_count(tmp_path):
    """DuckDB's profile reports the copy's returned-row count (1), not the
    SELECT's result size, so the runner overwrites `rows_returned` with the true
    count. Guards that fixup -- and the result_bytes accounting beside it."""
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT * FROM range(37) t(n)", result_path)
    assert stats["row_count"] == 37
    assert stats["profile"] is not None
    assert stats["profile"]["summary"]["rows_returned"] == 37
    assert stats["result_bytes"] == result_path.stat().st_size


def test_iceberg_metadata_parses_snapshot_and_deletes():
    """The probe maps iceberg_snapshots + iceberg_metadata rows to the wire
    shape, classifying data/delete files via the newer `manifest_content`."""
    from agent.executor.runner import _iceberg_metadata

    class FakeConn:
        # Newer iceberg extension: `manifest_content` carries DATA/DELETE.
        description = [("manifest_content",), ("count",)]

        def execute(self, sql):
            return self

        def fetchone(self):  # iceberg_snapshots row
            return (123456789, 1715780580000)

        def fetchall(self):  # iceberg_metadata grouped by manifest_content
            return [("DATA", 128), ("DELETE", 2)]

    meta = _iceberg_metadata(FakeConn(), "cat", "analytics", "events")
    assert meta["snapshot_id"] == 123456789
    assert meta["snapshot_at"].startswith("2024-")
    assert meta["data_file_count"] == 128
    assert meta["has_deletes"] is True


def test_iceberg_metadata_legacy_content_schema():
    """Older iceberg extensions without `manifest_content` classify via the
    `content` column (DATA/POSITION_DELETES/EQUALITY_DELETES)."""
    from agent.executor.runner import _iceberg_metadata

    class FakeConn:
        description = [("content",), ("count",)]

        def execute(self, sql):
            return self

        def fetchone(self):
            return (1, 1715780580000)

        def fetchall(self):
            return [("DATA", 5), ("POSITION_DELETES", 1)]

    meta = _iceberg_metadata(FakeConn(), "cat", "analytics", "events")
    assert meta["data_file_count"] == 5
    assert meta["has_deletes"] is True


def test_iceberg_metadata_best_effort_on_failure():
    """A probe failure (e.g. an older iceberg extension) degrades to all-null."""
    from agent.executor.runner import _iceberg_metadata

    class BoomConn:
        def execute(self, sql):
            raise RuntimeError("no such function: iceberg_snapshots")

    assert _iceberg_metadata(BoomConn(), "cat", "analytics", "events") == {
        "snapshot_id": None,
        "snapshot_at": None,
        "data_file_count": None,
        "has_deletes": None,
    }


def test_select_captures_normalized_profile(tmp_path):
    """A materialized SELECT returns a normalized profile: query summary with
    latency + peak memory, and an operator tree with actual cardinalities."""
    result_path = tmp_path / "out.parquet"
    stats = _run(
        "SELECT x % 10 g, count(*) c FROM range(50000) t(x) GROUP BY g ORDER BY c",
        result_path,
        enable_profiling=True,
    )
    profile = stats["profile"]
    assert profile is not None
    summary = profile["summary"]
    assert summary["latency_ms"] > 0
    assert summary["peak_memory_bytes"] >= 0
    assert set(summary) >= {"latency_ms", "peak_memory_bytes", "spill_bytes"}
    # rows_returned reflects the real result size (10 groups), not the COPY's
    # returned-row count of 1.
    assert summary["rows_returned"] == 10 == stats["row_count"]
    # The admission reservation the query ran under is recorded on the summary.
    assert summary["reserved_memory_bytes"] == _MEM
    assert summary["reserved_threads"] == _THREADS
    assert profile["tree"]["type"]  # operator tree present


def test_profiling_disabled_yields_null_profile(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT 1 AS x", result_path, enable_profiling=False)
    assert stats["profile"] is None


def test_ddl_has_no_profile(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("CREATE TABLE t (x INT)", result_path, enable_profiling=True)
    assert stats["profile"] is None


def test_profile_temp_file_cleaned_up(tmp_path):
    result_path = tmp_path / "out.parquet"
    _run("SELECT 1 AS x", result_path, enable_profiling=True)
    assert not (tmp_path / "out.profile.json").exists()


def test_forced_spill_reports_spill_bytes(tmp_path):
    """A large sort under a tiny memory_limit spills to a temp dir; the profile's
    query-level spill_bytes is positive (DuckDB reports spill at the root)."""
    import duckdb

    conn = duckdb.connect()
    conn.execute(f"SET temp_directory='{tmp_path / 'spill'}'")
    conn.execute("CREATE TABLE big AS SELECT i id, 'str' || i s FROM range(3000000) t(i)")
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync(
        "SELECT s, count(*) c FROM big GROUP BY s ORDER BY c DESC, s",
        result_path,
        memory_bytes=200 * 1024**2,  # 200 MB forces spill
        threads=2,
        conn=conn,
        enable_profiling=True,
    )
    assert stats["profile"]["summary"]["spill_bytes"] > 0


def test_capture_profile_best_effort_on_missing_or_bad_file(tmp_path):
    """A missing or malformed profile file degrades to None, never raises."""
    import duckdb

    from agent.executor.runner import _capture_profile

    conn = duckdb.connect()
    assert _capture_profile(conn, tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _capture_profile(conn, bad) is None


class _HealthConn:
    """Routes the health probe's queries by inspecting the SQL text.

    ``iceberg_metadata`` has both a ``LIMIT 0`` column-introspection call and an
    aggregate call; ``iceberg_snapshots`` is queried three ways (count, latest
    snapshot id, oldest timestamp); ``glob`` lists the data and metadata
    directories for orphan detection.
    """

    def __init__(
        self,
        *,
        files,
        listed=None,
        metadata_listed=None,
        columns=("manifest_content", "file_path", "manifest_path", "file_size_in_bytes"),
        parquet_sizes=None,
        snapshot_count=7,
        latest_snapshot_id=123456789,
        oldest_timestamp_ms=None,
    ):
        self.files = files  # list of (file_path, manifest_path, size) for DATA files
        self.listed = listed or []
        self.metadata_listed = metadata_listed or []
        self._columns = columns
        self.parquet_sizes = parquet_sizes or []  # (file_name, size) from the footers
        self.snapshot_count = snapshot_count
        self.latest_snapshot_id = latest_snapshot_id
        self.oldest_timestamp_ms = oldest_timestamp_ms
        self._last = ""

    def execute(self, sql, *args):
        self._last = sql
        if "LIMIT 0" in sql:
            self.description = [(c,) for c in self._columns]
        return self

    def fetchone(self):
        if "min(timestamp_ms)" in self._last:
            return (self.oldest_timestamp_ms,)
        if "ORDER BY sequence_number DESC" in self._last:
            return (self.latest_snapshot_id,)
        if "iceberg_snapshots" in self._last:
            return (self.snapshot_count,)
        return None

    def fetchall(self):
        if "glob" in self._last:
            if "/metadata/" in self._last:
                return [(f,) for f in self.metadata_listed]
            return [(f,) for f in self.listed]
        if "parquet_metadata" in self._last:
            return self.parquet_sizes
        if "iceberg_metadata" in self._last:
            return self.files
        return []


def test_collect_table_health_computes_distribution():
    from agent.executor.runner import collect_table_health

    files = [
        ("s3://b/t/data/a.parquet", "m1", 10),
        ("s3://b/t/data/b.parquet", "m1", 200 * 1024**2),
        ("s3://b/t/data/c.parquet", "m2", 5),
    ]
    health = collect_table_health(
        _HealthConn(files=files), "cat", "analytics", "events", target_file_bytes=128 * 1024**2
    )
    assert health["snapshot_count"] == 7
    assert health["data_file_count"] == 3
    assert health["manifest_count"] == 2
    assert health["total_data_bytes"] == 10 + 200 * 1024**2 + 5
    # two of three files are below the 128 MiB target.
    assert health["small_file_ratio"] == round(2 / 3, 4)
    # orphans not requested -> left null.
    assert health["orphan_file_count"] is None


def test_collect_table_health_size_fallback_via_parquet():
    from agent.executor.runner import collect_table_health

    # No size column in iceberg_metadata (the DuckDB <= 1.5.3 reality): on the deep
    # tier, sizes are derived from the Parquet footers instead.
    files = [
        ("s3://b/t/data/a.parquet", "m1", None),
        ("s3://b/t/data/b.parquet", "m1", None),
        ("s3://b/t/data/c.parquet", "m2", None),
    ]
    conn = _HealthConn(
        files=files,
        listed=[f[0] for f in files],  # no orphans
        columns=("manifest_content", "file_path", "manifest_path"),  # no size column
        parquet_sizes=[
            ("a.parquet", 10),
            ("b.parquet", 200 * 1024**2),
            ("c.parquet", 5),
        ],
    )
    health = collect_table_health(
        conn, "cat", "analytics", "events", target_file_bytes=128 * 1024**2, include_orphans=True
    )
    assert health["total_data_bytes"] == 10 + 200 * 1024**2 + 5
    assert health["avg_file_bytes"] == (10 + 200 * 1024**2 + 5) // 3
    # two of three files are below the 128 MiB target.
    assert health["small_file_ratio"] == round(2 / 3, 4)


def test_collect_table_health_size_fallback_is_deep_tier_only():
    from agent.executor.runner import collect_table_health

    # Without the deep tier, a missing size column leaves sizes unmeasured rather
    # than paying the per-file footer reads on every cheap cycle.
    files = [("s3://b/t/data/a.parquet", "m1", None)]
    conn = _HealthConn(
        files=files,
        columns=("manifest_content", "file_path", "manifest_path"),
        parquet_sizes=[("a.parquet", 10)],
    )
    health = collect_table_health(
        conn, "cat", "analytics", "events", target_file_bytes=128 * 1024**2
    )
    assert health["data_file_count"] == 1
    assert health["total_data_bytes"] is None
    assert health["small_file_ratio"] is None


def test_collect_table_health_orphan_estimate():
    from agent.executor.runner import collect_table_health

    files = [
        ("s3://b/t/data/a.parquet", "m1", 100),
        ("s3://b/t/data/b.parquet", "m1", 100),
    ]
    listed = [
        "s3://b/t/data/a.parquet",
        "s3://b/t/data/b.parquet",
        "s3://b/t/data/orphan-1.parquet",
        "s3://b/t/data/orphan-2.parquet",
    ]
    health = collect_table_health(
        _HealthConn(files=files, listed=listed),
        "cat",
        "analytics",
        "events",
        target_file_bytes=128 * 1024**2,
        include_orphans=True,
    )
    assert health["orphan_file_count"] == 2
    # estimated from the live average file size (100 bytes each).
    assert health["orphan_bytes"] == 200


def test_collect_table_health_reports_snapshot_id_and_age():
    from agent.executor.runner import collect_table_health

    files = [("s3://b/t/data/a.parquet", "m1", 10)]
    oldest_ms = int(time.time() * 1000) - 3 * 86_400_000  # 3 days old
    conn = _HealthConn(files=files, latest_snapshot_id=123456789, oldest_timestamp_ms=oldest_ms)
    health = collect_table_health(
        conn, "cat", "analytics", "events", target_file_bytes=128 * 1024**2
    )
    assert health["snapshot_id"] == 123456789
    assert health["oldest_snapshot_age_days"] == pytest.approx(3.0, abs=0.01)


def test_collect_table_health_orphan_estimate_includes_metadata():
    from agent.executor.runner import collect_table_health

    # The live manifest path is referenced, so only the stray metadata file is
    # orphaned; the data directory has no orphans.
    files = [("s3://b/t/data/a.parquet", "s3://b/t/metadata/v1.metadata.json", 100)]
    conn = _HealthConn(
        files=files,
        listed=["s3://b/t/data/a.parquet"],
        metadata_listed=[
            "s3://b/t/metadata/v1.metadata.json",
            "s3://b/t/metadata/orphan.metadata.json",
        ],
    )
    health = collect_table_health(
        conn, "cat", "analytics", "events", target_file_bytes=128 * 1024**2, include_orphans=True
    )
    assert health["orphan_file_count"] == 1
    assert health["orphan_bytes"] == 100


def test_collect_table_health_best_effort_on_failure():
    from agent.executor.runner import collect_table_health

    class BoomConn:
        def execute(self, sql, *args):
            raise RuntimeError("no iceberg extension")

    health = collect_table_health(BoomConn(), "cat", "analytics", "events", target_file_bytes=1)
    assert health["schema"] == "analytics"
    assert health["data_file_count"] is None
    assert health["snapshot_count"] is None


def test_stats_for_reports_table_row_count(tmp_path):
    """When asked, the runner reports the true table row count (size stays null)."""
    result_path = tmp_path / "out.parquet"

    def seed(conn):
        conn.execute("CREATE TABLE main.events AS SELECT * FROM range(3) t(id)")

    stats = _run(
        "SELECT * FROM main.events",
        result_path,
        stats_for={"catalog": "memory", "schema": "main", "table": "events"},
        on_connect=seed,
    )
    assert stats["row_count"] == 3
    assert stats["table_row_count"] == 3
    assert stats["table_size_bytes"] is None


# ── per-statement peak/spill on a held connection ─────────────────────────────


def test_second_statement_on_a_held_connection_reports_its_own_peak(tmp_path):
    """DuckDB's peak-memory metric is a high-water mark for the whole connection,
    not the statement. A held SQL session reuses one connection for its whole
    life, so before this every statement after the first reported the heaviest
    earlier statement's peak as its own — wrong numbers in the profile UI for any
    query that was not the first one run on a session."""
    import duckdb

    from agent.executor.runner import run_statement_sync

    conn = duckdb.connect()
    watermarks: dict[str, int] = {}

    heavy = run_statement_sync(
        "SELECT i % 3000000 g, count(*) c FROM range(5000000) t(i) GROUP BY 1 ORDER BY c DESC",
        tmp_path / "heavy.parquet",
        conn=conn,
        memory_bytes=_MEM,
        threads=_THREADS,
        watermarks=watermarks,
    )
    heavy_peak = heavy["profile"]["summary"]["peak_memory_bytes"]
    assert heavy_peak > 0, "the heavy statement should report a real peak"

    light = run_statement_sync(
        "SELECT 1 AS n",
        tmp_path / "light.parquet",
        conn=conn,
        memory_bytes=_MEM,
        threads=_THREADS,
        watermarks=watermarks,
    )
    light_summary = light["profile"]["summary"]

    assert light_summary["peak_memory_bytes"] < heavy_peak, (
        "SELECT 1 must not inherit the previous statement's peak"
    )
    # total_memory_allocated is DuckDB's only genuinely per-statement memory
    # metric, and is what the UI can show when the delta is 0.
    assert (
        light_summary["memory_allocated_bytes"]
        < heavy["profile"]["summary"]["memory_allocated_bytes"]
    )


def test_one_shot_queries_still_report_the_raw_peak(tmp_path):
    """The watermark subtraction must not change the per-query path: a fresh
    connection starts at zero, so the delta is the raw value."""
    result_path = tmp_path / "out.parquet"
    stats = _run(
        "SELECT i % 100000 g, count(*) FROM range(500000) t(i) GROUP BY 1",
        result_path,
        enable_profiling=True,
    )
    assert stats["profile"]["summary"]["peak_memory_bytes"] > 0


# ── is_cheap_statement: ANALYZE and VACUUM share a DuckDB statement type ──────
#
# DuckDB types both bare `ANALYZE` and `ANALYZE <table>` as
# `StatementType.VACUUM` -- the same type a real `VACUUM` gets -- so `ANALYZE`
# used to be listed as its own cheap statement type and never matched anything,
# silently falling to the fallback bucket (a third of the whole agent) for a
# statement that moves no data.


def test_analyze_is_cheap():
    from agent.executor.runner import is_cheap_statement

    assert is_cheap_statement("ANALYZE")
    assert is_cheap_statement("ANALYZE my_table")
    assert is_cheap_statement("analyze my_table")  # case-insensitive


def test_plain_vacuum_is_not_cheap():
    """Only ANALYZE is known to be cheap; a real VACUUM shares ANALYZE's
    statement type but isn't assumed cheap without evidence either way."""
    from agent.executor.runner import is_cheap_statement

    assert not is_cheap_statement("VACUUM")


def test_set_and_transaction_are_still_cheap():
    """Unchanged by the ANALYZE/VACUUM disambiguation."""
    from agent.executor.runner import is_cheap_statement

    assert is_cheap_statement("SET threads=2")
    assert is_cheap_statement("BEGIN TRANSACTION")


def test_a_select_is_not_cheap():
    from agent.executor.runner import is_cheap_statement

    assert not is_cheap_statement("SELECT 1")
