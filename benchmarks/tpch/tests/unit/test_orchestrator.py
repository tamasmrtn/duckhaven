from __future__ import annotations

import threading
import time

import pytest

from tpch_bench.clients.base import EngineClient, QueryResult
from tpch_bench.ledger.store import Ledger
from tpch_bench.ledger.wal import WalWriter, read_events
from tpch_bench.orchestrator import scenario_cold_start, scenario_concurrent, scenario_sequential
from tpch_bench.orchestrator.runner import (
    RunContext,
    pending_query_work_items,
    query_work_item_id,
    register_query_work_items,
    run_query_work_item,
)

QUERIES = {"q01": "SELECT 1", "q02": "SELECT 2", "q03": "SELECT 3"}


class FakeEngineClient(EngineClient):
    def __init__(self, fail_sql: set[str] | None = None, lock: threading.Lock | None = None):
        self.connect_calls = 0
        self.close_calls = 0
        self.executed: list[str] = []
        self._fail_sql = fail_sql or set()
        self._lock = lock

    def connect(self) -> None:
        self.connect_calls += 1

    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        if self._lock is not None:
            with self._lock:
                self.executed.append(sql)
        else:
            self.executed.append(sql)
        if sql in self._fail_sql:
            return QueryResult(error=f"boom: {sql}")
        return QueryResult(client_wall_ms=1.0, row_count=len(sql))

    def close(self) -> None:
        self.close_calls += 1


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


# ── runner.py ────────────────────────────────────────────────────────────


def test_register_query_work_items_covers_every_query_and_rep(ctx):
    items = register_query_work_items(ctx, scenario="sequential", query_ids=["q01", "q02"], reps=2)

    assert len(items) == 4
    assert set(items.values()) == {("q01", 0), ("q01", 1), ("q02", 0), ("q02", 1)}
    for item_id in items:
        assert ctx.ledger.status(item_id) == "pending"


def test_pending_query_work_items_excludes_already_done_items(ctx):
    items = register_query_work_items(ctx, scenario="sequential", query_ids=["q01", "q02"], reps=1)
    done_id = query_work_item_id(ctx, scenario="sequential", query_id="q01", rep=0)
    ctx.ledger.mark_running(done_id)
    ctx.ledger.mark_done(done_id)

    pending = pending_query_work_items(ctx, items)

    assert done_id not in pending
    assert set(pending.values()) == {("q02", 0)}


def test_run_query_work_item_marks_done_and_writes_wal_events(ctx, tmp_path):
    item_id = query_work_item_id(ctx, scenario="sequential", query_id="q01", rep=0)
    ctx.ledger.register_work_item(
        work_item_id=item_id,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=0,
    )
    client = FakeEngineClient()

    result = run_query_work_item(ctx, client, item_id=item_id, sql="SELECT 1")

    assert result.error is None
    assert ctx.ledger.status(item_id) == "done"
    events = read_events(tmp_path / "wal.jsonl")
    tables = [e["table"] for e in events]
    assert tables == ["work_items", "query_results", "work_items"]
    assert events[0]["row"]["status"] == "running"
    assert events[-1]["row"]["status"] == "done"


def test_run_query_work_item_marks_failed_on_a_query_error(ctx):
    item_id = query_work_item_id(ctx, scenario="sequential", query_id="q01", rep=0)
    ctx.ledger.register_work_item(
        work_item_id=item_id,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=0,
    )
    client = FakeEngineClient(fail_sql={"SELECT 1"})

    result = run_query_work_item(ctx, client, item_id=item_id, sql="SELECT 1")

    assert result.error is not None
    assert ctx.ledger.status(item_id) == "failed"


# ── scenario_sequential ──────────────────────────────────────────────────


def test_sequential_connects_once_and_runs_every_query(ctx):
    client = FakeEngineClient()

    scenario_sequential.run(ctx, client, QUERIES, reps=1)

    assert client.connect_calls == 1
    assert sorted(client.executed) == sorted(QUERIES.values())
    for query_id in QUERIES:
        item_id = query_work_item_id(ctx, scenario="sequential", query_id=query_id, rep=0)
        assert ctx.ledger.status(item_id) == "done"


def test_sequential_skips_work_already_done_on_a_resumed_run(ctx):
    done_id = query_work_item_id(ctx, scenario="sequential", query_id="q01", rep=0)
    ctx.ledger.register_work_item(
        work_item_id=done_id,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=0,
    )
    ctx.ledger.mark_running(done_id)
    ctx.ledger.mark_done(done_id)
    client = FakeEngineClient()

    scenario_sequential.run(ctx, client, QUERIES, reps=1)

    assert QUERIES["q01"] not in client.executed
    assert QUERIES["q02"] in client.executed


def test_sequential_is_a_no_op_when_everything_is_already_done(ctx):
    scenario_sequential.run(ctx, FakeEngineClient(), {"q01": "SELECT 1"}, reps=1)
    client = FakeEngineClient()

    scenario_sequential.run(ctx, client, {"q01": "SELECT 1"}, reps=1)

    assert client.connect_calls == 0
    assert client.executed == []


# ── scenario_cold_start ──────────────────────────────────────────────────


def test_cold_start_reconnects_once_per_query(ctx):
    client = FakeEngineClient()

    scenario_cold_start.run(ctx, client, QUERIES, reps=1)

    assert client.connect_calls == len(QUERIES)
    assert client.close_calls == len(QUERIES)
    assert sorted(client.executed) == sorted(QUERIES.values())


# ── scenario_concurrent ───────────────────────────────────────────────────


def test_concurrent_gives_each_worker_its_own_client(ctx):
    lock = threading.Lock()
    created: list[FakeEngineClient] = []

    def factory() -> FakeEngineClient:
        client = FakeEngineClient(lock=lock)
        created.append(client)
        return client

    scenario_concurrent.run(ctx, factory, QUERIES, reps=1, max_workers=4)

    assert len(created) == len(QUERIES)
    for client in created:
        assert client.connect_calls == 1
        assert client.close_calls == 1
    all_executed = sorted(sql for client in created for sql in client.executed)
    assert all_executed == sorted(QUERIES.values())


def test_concurrent_never_exceeds_one_reps_worth_of_in_flight_queries(ctx):
    # Regression: reps used to be flattened into one ThreadPoolExecutor
    # submission, so reps=3 silently fired len(QUERIES)*3 queries at once
    # instead of three rounds of len(QUERIES) — a real crash against a
    # small DuckHaven agent at SF10 traced back to exactly this. Each rep
    # must run to completion before the next one starts.
    peak = {"n": 0, "max": 0}
    counter_lock = threading.Lock()

    class TrackingClient(FakeEngineClient):
        def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
            with counter_lock:
                peak["n"] += 1
                peak["max"] = max(peak["max"], peak["n"])
            time.sleep(0.02)  # widen the window for real overlap to show up
            result = super().run_statement(sql, timeout_s=timeout_s)
            with counter_lock:
                peak["n"] -= 1
            return result

    def factory() -> TrackingClient:
        return TrackingClient()

    scenario_concurrent.run(ctx, factory, QUERIES, reps=3)

    assert peak["max"] <= len(QUERIES)


def test_concurrent_marks_every_work_item_done_without_corrupting_the_ledger(ctx):
    lock = threading.Lock()

    def factory() -> FakeEngineClient:
        return FakeEngineClient(lock=lock)

    scenario_concurrent.run(ctx, factory, QUERIES, reps=2, max_workers=6)

    for query_id in QUERIES:
        for rep in range(2):
            item_id = query_work_item_id(ctx, scenario="concurrent", query_id=query_id, rep=rep)
            assert ctx.ledger.status(item_id) == "done"


def test_concurrent_propagates_a_worker_exception(ctx):
    def factory() -> FakeEngineClient:
        client = FakeEngineClient()

        def boom(sql, *, timeout_s):
            raise RuntimeError("connection lost")

        client.run_statement = boom  # type: ignore[method-assign]
        return client

    with pytest.raises(RuntimeError, match="connection lost"):
        scenario_concurrent.run(ctx, factory, {"q01": "SELECT 1"}, reps=1)


def test_concurrent_records_a_connect_failure_without_crashing_the_round(ctx):
    # Regression: N independent connection attempts firing at once (this
    # scenario's whole point) means some failing is a real, expected
    # outcome — a session-open timeout under load, say — not a reason to
    # crash every other query in the same round. Before
    # record_connection_failure existed, a raised connect() propagated out
    # of the worker thread, and ThreadPoolExecutor's future.result() then
    # re-raised it on the caller's thread, aborting the whole round and
    # leaving every other item stuck `pending`.
    assignment_lock = threading.Lock()
    remaining = {"n": 1}  # exactly one client, whichever grabs it first, fails to connect

    def factory() -> FakeEngineClient:
        client = FakeEngineClient()
        with assignment_lock:
            should_fail = remaining["n"] > 0
            if should_fail:
                remaining["n"] -= 1
        if should_fail:

            def boom() -> None:
                raise RuntimeError("session open_timeout")

            client.connect = boom  # type: ignore[method-assign]
        return client

    scenario_concurrent.run(ctx, factory, QUERIES, reps=1)

    statuses = {
        query_id: ctx.ledger.status(
            query_work_item_id(ctx, scenario="concurrent", query_id=query_id, rep=0)
        )
        for query_id in QUERIES
    }
    assert sorted(statuses.values()) == ["done", "done", "failed"]
