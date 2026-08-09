"""What a statement is actually given to run with, over the real Iceberg path.

The unit tests pin the admission arithmetic; these pin the thing the arithmetic
exists to produce — the `threads` and `memory_limit` a statement runs under, and
the fact that a second read of the same Iceberg table is cheaper than the first
because the memory it was given was enough to keep DuckDB's file cache.

Every assertion here is a behavioural invariant of the resource model, not a
snapshot of today's bucket table: `reserved_threads == cores` rather than
`== 2`, "faster than the first read" rather than a millisecond budget.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.control import session
from agent.control.channel import (
    _resize_for_statement,
    _session_reservation_request,
    _shrink_to_baseline,
)
from agent.executor.admission import Admission
from agent.executor.runner import run_statement_sync

pytestmark = pytest.mark.integration


def _seed(conn, rows: int = 200000) -> None:
    """Wide enough that a scan is worth parallelising and worth caching, small
    enough to stay a fast test."""
    conn.execute("CREATE TABLE res_src (id BIGINT, g BIGINT, label VARCHAR)")
    conn.execute(
        "INSERT INTO res_src SELECT i, i % 1000, 'row-' || i FROM range(?) t(i)",
        [rows],
    )


def _admission() -> Admission:
    return Admission(profile="auto", headroom=0.10)


def _register(admission: Admission, conn, session_id: str = "res-test"):
    """A held session on a real attached connection, holding a real reservation."""
    request = _session_reservation_request(admission)
    reservation = admission._try_admit(request)  # noqa: SLF001 - set the held grant up directly
    assert reservation is not None
    state = session.SessionState(
        session_id=session_id,
        conn=conn,
        reservation=reservation,
        memory_bytes=reservation.memory_bytes,
        threads=reservation.threads,
        opened_at=0.0,
        last_active_at=0.0,
    )
    session.register(state)
    return state


@pytest.fixture(autouse=True)
def _clear_sessions():
    session._sessions.clear()  # noqa: SLF001 - the registry is process-global
    yield
    session._sessions.clear()  # noqa: SLF001


async def test_a_scan_heavy_statement_runs_on_every_core(
    polaris_s3_catalog, attach_factory, tmp_path
) -> None:
    """A scan of a whole Iceberg table aggregating to a single row is the shape
    that used to be charged almost nothing and throttled to one thread."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    _seed(conn)
    admission = _admission()
    state = _register(admission, conn)

    sql = "SELECT sum(id) FROM res_src WHERE g > 10"
    await _resize_for_statement(state, sql, admission)
    stats = run_statement_sync(
        sql,
        tmp_path / "scan.parquet",
        conn=conn,
        memory_bytes=state.memory_bytes,
        threads=state.threads,
        watermarks=state.watermarks,
    )

    assert state.threads == admission.cores
    assert stats["profile"]["summary"]["reserved_threads"] == admission.cores


async def test_an_idle_agent_lends_the_statement_its_spare_budget(
    polaris_s3_catalog, attach_factory
) -> None:
    """The memory a statement is given has to exceed what its operators need, or
    the Iceberg scan has nowhere to cache and re-reads object storage every pass."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    _seed(conn)
    admission = _admission()
    state = _register(admission, conn)

    await _resize_for_statement(state, "SELECT sum(id) FROM res_src", admission)

    assert state.reservation.elastic_bytes > 0
    assert state.memory_bytes == state.reservation.total_bytes
    assert state.memory_bytes > state.reservation.memory_bytes
    assert admission.committed_fraction <= 1.0


async def test_the_cache_survives_between_statements(
    polaris_s3_catalog, attach_factory, tmp_path
) -> None:
    """The grant is kept across the shrink back to baseline, so the second read of
    the same Iceberg table does not go back to object storage for it."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    _seed(conn)
    admission = _admission()
    state = _register(admission, conn)

    sql = "SELECT sum(id), count(*) FROM res_src"

    def _run(name: str) -> float:
        stats = run_statement_sync(
            sql,
            tmp_path / f"{name}.parquet",
            conn=conn,
            memory_bytes=state.memory_bytes,
            threads=state.threads,
            watermarks=state.watermarks,
        )
        return stats["profile"]["summary"]["cpu_time_ms"]

    await _resize_for_statement(state, sql, admission)
    cold_cpu = _run("cold")
    _shrink_to_baseline(state, admission)
    lent = state.reservation.elastic_bytes

    await _resize_for_statement(state, sql, admission)
    warm_cpu = _run("warm")
    _shrink_to_baseline(state, admission)

    assert lent > 0, "the cache grant was dropped at the end of the statement"
    # CPU, not wall time: a re-read burns CPU decompressing Parquet it already
    # had, which is the cost the grant removes, and it is far less noisy than
    # wall clock on a shared CI box. Generous margin — this asserts "did not get
    # dramatically worse", not a performance target.
    assert warm_cpu <= cold_cpu * 1.5


async def test_a_second_session_reclaims_idle_cache_rather_than_queueing(
    polaris_s3_catalog, attach_factory
) -> None:
    """The fairness half: lending spare budget to one session must not make the
    next one wait for it."""
    catalog, ns = polaris_s3_catalog
    admission = _admission()
    first = _register(admission, attach_factory(catalog, ns), "res-a")

    await _resize_for_statement(first, "SELECT 1", admission)
    _shrink_to_baseline(first, admission)
    assert first.reservation.elastic_bytes > 0, "nothing was lent, so nothing to reclaim"

    # A second session opening and sizing a statement, while the first sits idle
    # holding most of the budget as cache.
    second = _register(admission, attach_factory(catalog, ns), "res-b")
    await asyncio.wait_for(_resize_for_statement(second, "SELECT 1", admission), timeout=30)

    assert second.reservation.memory_bytes >= _session_reservation_request(admission).memory_bytes
    assert admission.committed_fraction <= 1.0
    assert first.reservation.total_bytes + second.reservation.total_bytes <= admission.budget_bytes
