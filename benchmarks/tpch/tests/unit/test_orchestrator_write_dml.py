from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from tpch_bench.clients.base import EngineClient, QueryResult
from tpch_bench.ledger.store import Ledger
from tpch_bench.ledger.wal import WalWriter
from tpch_bench.orchestrator import scenario_dml, scenario_write
from tpch_bench.orchestrator.runner import RunContext, query_work_item_id
from tpch_bench.orchestrator.scenario_write import target_table_name

DDL_DIR = Path(__file__).resolve().parents[2] / "ddl" / "duckhaven"
NARROW_SQL = (DDL_DIR / "narrow.sql").read_text()
WIDE_SQL = (DDL_DIR / "wide.sql").read_text()


class FakeEngineClient(EngineClient):
    def __init__(self, fail_sql: set[str] | None = None):
        self.connect_calls = 0
        self.executed: list[str] = []
        self._fail_sql = fail_sql or set()

    def connect(self) -> None:
        self.connect_calls += 1

    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        self.executed.append(sql)
        if sql in self._fail_sql:
            return QueryResult(error="boom")
        return QueryResult(client_wall_ms=1.0)

    def close(self) -> None:
        pass


class DuckDBBackedClient(EngineClient):
    """Runs statements against a real, in-memory DuckDB connection loaded
    with tiny tpch data — an integration check that the generated DDL/DML
    SQL text is actually valid, which a fake client's bookkeeping-only
    tests can't catch."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    def connect(self) -> None:
        pass

    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        try:
            self.con.execute(sql)
        except duckdb.Error as exc:
            return QueryResult(error=str(exc))
        return QueryResult(client_wall_ms=1.0)

    def close(self) -> None:
        pass


@pytest.fixture
def ctx(tmp_path):
    with Ledger(":memory:") as ledger:
        with WalWriter(tmp_path / "wal.jsonl") as wal:
            yield RunContext(
                ledger=ledger,
                wal=wal,
                engine="duckhaven",
                scale_factor=1,
                run_id="run-1",
                methodology_hash="hash-1",
                query_timeout_s=30.0,
            )


@pytest.fixture
def duckdb_con():
    con = duckdb.connect()
    con.execute("INSTALL tpch; LOAD tpch; CALL dbgen(sf=0.01);")
    yield con
    con.close()


# ── scenario_write: orchestration (FakeEngineClient) ────────────────────


def test_write_issues_the_duckhaven_pre_statement_before_each_ctas(ctx):
    client = FakeEngineClient()

    scenario_write.run(
        ctx,
        client,
        {"narrow": NARROW_SQL},
        reps=1,
        duckhaven_pre_statement="SET duckhaven_concurrency = 'single'",
    )

    assert client.executed[0] == "SET duckhaven_concurrency = 'single'"
    assert "tpch_write_narrow_r0" in client.executed[1]


def test_write_targets_a_distinct_table_per_rep(ctx):
    client = FakeEngineClient()

    scenario_write.run(ctx, client, {"narrow": NARROW_SQL}, reps=3)

    assert "tpch_write_narrow_r0" in client.executed[0]
    assert "tpch_write_narrow_r1" in client.executed[1]
    assert "tpch_write_narrow_r2" in client.executed[2]
    for query_id, rep in (("narrow", 0), ("narrow", 1), ("narrow", 2)):
        item_id = query_work_item_id(ctx, scenario="write", query_id=query_id, rep=rep)
        assert ctx.ledger.status(item_id) == "done"


def test_write_skips_reps_already_done_on_a_resumed_run(ctx):
    scenario_write.run(ctx, FakeEngineClient(), {"narrow": NARROW_SQL}, reps=2)
    client = FakeEngineClient()

    scenario_write.run(ctx, client, {"narrow": NARROW_SQL}, reps=2)

    assert client.executed == []


# ── scenario_dml: orchestration (FakeEngineClient) ───────────────────────


def test_dml_runs_delete_before_insert_within_each_cycle(ctx):
    client = FakeEngineClient()

    scenario_dml.run(ctx, client, {"narrow": NARROW_SQL}, cycles=2)

    assert len(client.executed) == 4
    assert client.executed[0].startswith("DELETE FROM tpch_write_narrow_r0")
    assert client.executed[0].endswith("% 100 = 0")
    assert client.executed[1].startswith("INSERT INTO tpch_write_narrow_r0")
    assert "% 100 = 0" in client.executed[1]
    assert client.executed[2].startswith("DELETE FROM tpch_write_narrow_r0")
    assert client.executed[2].endswith("% 100 = 1")


def test_dml_skips_cycles_already_done_on_a_resumed_run(ctx):
    scenario_dml.run(ctx, FakeEngineClient(), {"narrow": NARROW_SQL}, cycles=1)
    client = FakeEngineClient()

    scenario_dml.run(ctx, client, {"narrow": NARROW_SQL}, cycles=1)

    assert client.executed == []


def test_dml_marks_failed_but_does_not_raise_on_a_bad_statement(ctx):
    client = FakeEngineClient(fail_sql={_expected_delete_sql()})

    scenario_dml.run(ctx, client, {"narrow": NARROW_SQL}, cycles=1)

    delete_id = query_work_item_id(ctx, scenario="dml", query_id="narrow_delete", rep=0)
    assert ctx.ledger.status(delete_id) == "failed"


def test_dml_does_not_run_the_insert_when_its_delete_failed(ctx):
    # A failed delete leaves the rows it should have removed still there;
    # running the insert anyway would duplicate them rather than refresh
    # them (this is the exact bug the real-DuckDB round-trip test caught
    # before this guard existed).
    client = FakeEngineClient(fail_sql={_expected_delete_sql()})

    scenario_dml.run(ctx, client, {"narrow": NARROW_SQL}, cycles=1)

    insert_sql_ran = [sql for sql in client.executed if sql.startswith("INSERT")]
    assert insert_sql_ran == []
    insert_id = query_work_item_id(ctx, scenario="dml", query_id="narrow_insert", rep=0)
    assert ctx.ledger.status(insert_id) == "pending"


def _expected_delete_sql() -> str:
    return "DELETE FROM tpch_write_narrow_r0 WHERE l_orderkey % 100 = 0"


# ── Real DuckDB integration: the generated SQL is actually valid ────────


def test_write_and_dml_round_trip_against_real_duckdb(ctx, duckdb_con):
    client = DuckDBBackedClient(duckdb_con)

    scenario_write.run(ctx, client, {"narrow": NARROW_SQL, "wide": WIDE_SQL}, reps=1)

    narrow_table = target_table_name("narrow", 0)
    wide_table = target_table_name("wide", 0)
    before_narrow = duckdb_con.execute(f"SELECT count(*) FROM {narrow_table}").fetchone()[0]
    before_wide = duckdb_con.execute(f"SELECT count(*) FROM {wide_table}").fetchone()[0]
    assert before_narrow > 0
    assert before_wide == before_narrow

    scenario_dml.run(ctx, client, {"narrow": NARROW_SQL, "wide": WIDE_SQL}, cycles=3)

    after_narrow = duckdb_con.execute(f"SELECT count(*) FROM {narrow_table}").fetchone()[0]
    after_wide = duckdb_con.execute(f"SELECT count(*) FROM {wide_table}").fetchone()[0]
    assert after_narrow == before_narrow
    assert after_wide == before_wide

    for shape in ("narrow", "wide"):
        for cycle in range(3):
            for suffix in ("delete", "insert"):
                item_id = query_work_item_id(
                    ctx, scenario="dml", query_id=f"{shape}_{suffix}", rep=cycle
                )
                assert ctx.ledger.status(item_id) == "done"
