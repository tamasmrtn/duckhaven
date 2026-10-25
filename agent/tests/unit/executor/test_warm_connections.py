"""Warm spares for `open_and_attach` (executor.warm_connections)."""

import os
import threading
import time

import duckdb
import pytest

from agent.executor import runner
from agent.executor.warm_connections import _MAX_SHAPES, Prepared, WarmConnections

_EMPTY = (frozenset(), frozenset())


def _wait_for_spares(pool: WarmConnections, shape, count: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with pool._lock:
            if len(pool._spares.get(shape, [])) >= count:
                return
        time.sleep(0.01)
    raise AssertionError(f"no {count} spare(s) for {shape} within 5 s")


def _counting_prepare():
    made: list[duckdb.DuckDBPyConnection] = []

    def prepare(shape):
        conn = duckdb.connect()
        made.append(conn)
        return Prepared(conn, frozenset({f"ext-{shape}"}))

    return prepare, made


def test_a_miss_returns_none_and_the_next_take_gets_a_spare():
    prepare, made = _counting_prepare()
    pool = WarmConnections(prepare, per_shape=1)
    try:
        assert pool.take("a") is None
        _wait_for_spares(pool, "a", 1)
        prepared = pool.take("a")
        assert prepared is not None
        assert prepared.conn is made[0]
        assert prepared.loaded == frozenset({"ext-a"})
    finally:
        pool.close()


def test_a_spare_is_handed_out_once():
    prepare, made = _counting_prepare()
    pool = WarmConnections(prepare, per_shape=1)
    try:
        pool.take("a")
        _wait_for_spares(pool, "a", 1)
        first = pool.take("a")
        second = pool.take("a")  # the refill may not have landed yet
        assert first is not None
        assert second is None or second.conn is not first.conn
    finally:
        pool.close()


def test_a_spare_waits_with_one_thread_and_wakes_with_its_own():
    prepare, _ = _counting_prepare()
    pool = WarmConnections(prepare, per_shape=1)
    try:
        pool.take("a")
        _wait_for_spares(pool, "a", 1)
        with pool._lock:
            spare = pool._spares["a"][0]
        idle = spare.prepared.conn.execute("SELECT current_setting('threads')").fetchone()[0]
        assert idle == 1
        prepared = pool.take("a")
        woken = prepared.conn.execute("SELECT current_setting('threads')").fetchone()[0]
        assert (
            woken
            == spare.threads
            == duckdb.connect().execute("SELECT current_setting('threads')").fetchone()[0]
        )
    finally:
        pool.close()


def test_shapes_are_kept_apart():
    prepare, _ = _counting_prepare()
    pool = WarmConnections(prepare, per_shape=1)
    try:
        pool.take("a")
        pool.take("b")
        _wait_for_spares(pool, "a", 1)
        _wait_for_spares(pool, "b", 1)
        assert pool.take("a").loaded == frozenset({"ext-a"})
        assert pool.take("b").loaded == frozenset({"ext-b"})
    finally:
        pool.close()


def test_the_least_recently_used_shape_gives_its_spares_up():
    prepare, made = _counting_prepare()
    pool = WarmConnections(prepare, per_shape=1)
    try:
        pool.take("old")
        _wait_for_spares(pool, "old", 1)
        old_conn = made[0]
        for i in range(_MAX_SHAPES):
            pool.take(f"new-{i}")
        with pool._lock:
            assert "old" not in pool._spares
        with pytest.raises(duckdb.ConnectionException):
            old_conn.execute("SELECT 1")
    finally:
        pool.close()


@pytest.mark.skipif(not hasattr(os, "sched_getaffinity"), reason="Linux only")
def test_a_fill_runs_on_one_cpu_and_leaves_the_caller_alone():
    cpus: list[set[int]] = []

    def prepare(shape):
        cpus.append(os.sched_getaffinity(0))
        return Prepared(duckdb.connect(), frozenset())

    before = os.sched_getaffinity(0)
    pool = WarmConnections(prepare, per_shape=1)
    try:
        pool.take("a")
        _wait_for_spares(pool, "a", 1)
        assert len(cpus[0]) == 1
        assert cpus[0] <= before
        assert os.sched_getaffinity(0) == before
    finally:
        pool.close()


def test_a_failed_prepare_is_a_miss_and_is_retried():
    calls = 0

    def flaky(shape):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("extension directory unreadable")
        return Prepared(duckdb.connect(), frozenset())

    pool = WarmConnections(flaky, per_shape=1)
    try:
        assert pool.take("a") is None  # schedules the failing fill
        deadline = time.monotonic() + 5
        while calls < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        assert pool.take("a") is None  # nothing kept from the failure; refills again
        _wait_for_spares(pool, "a", 1)
        assert pool.take("a") is not None
    finally:
        pool.close()


def test_close_closes_the_spares_and_stops_refilling():
    prepare, made = _counting_prepare()
    pool = WarmConnections(prepare, per_shape=1)
    pool.take("a")
    _wait_for_spares(pool, "a", 1)
    pool.close()
    with pytest.raises(duckdb.ConnectionException):
        made[0].execute("SELECT 1")
    assert pool.take("a") is None
    time.sleep(0.05)
    assert len(made) == 1


def test_a_fill_finishing_after_close_is_closed_not_kept():
    release = threading.Event()
    made: list[duckdb.DuckDBPyConnection] = []

    def slow(shape):
        release.wait(5)
        conn = duckdb.connect()
        made.append(conn)
        return Prepared(conn, frozenset())

    pool = WarmConnections(slow, per_shape=1)
    pool.take("a")
    pool.close()
    release.set()
    deadline = time.monotonic() + 5
    while not made and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    if made:  # shutdown(cancel_futures) may have run the fill anyway
        with pytest.raises(duckdb.ConnectionException):
            made[0].execute("SELECT 1")


def test_open_and_attach_takes_a_spare_and_still_locks_it():
    """The spare stands in for the load phase only: the sandbox is applied to the
    connection the caller gets, exactly as for one opened on the spot."""
    runner.enable_warm_connections(1)
    first = runner.open_and_attach(lock_config=True)  # a miss: opened on the spot
    _wait_for_spares(runner._warm, _EMPTY, 1)
    with runner._warm._lock:
        spare_conn = runner._warm._spares[_EMPTY][0].prepared.conn
    second = runner.open_and_attach(lock_config=True)
    assert second is spare_conn
    assert second is not first
    with pytest.raises(duckdb.Error):
        second.execute("SET allow_unsigned_extensions=true")
    second.execute("SET threads=2")  # what the runner sets per statement still works


def test_open_and_attach_opens_on_the_spot_when_the_pool_is_off():
    runner.enable_warm_connections(0)
    assert runner._warm is None
    conn = runner.open_and_attach()
    conn.execute("SELECT 1")
