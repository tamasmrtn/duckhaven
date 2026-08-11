"""supervisor.run_query's trace-context handoff across run_in_executor.

`loop.run_in_executor` does not propagate contextvars to the worker thread, so
`trace.get_current_span()` returns nothing there. run_query must capture the
active span's W3C carrier on the event-loop thread and pass it into
run_query_sync explicitly, not rely on the worker thread seeing it itself.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb
import pytest
from opentelemetry import trace

from agent.executor import supervisor as supervisor_module
from agent.executor.supervisor import StatementAbandoned


async def test_trace_headers_captured_on_event_loop_thread_reach_run_query_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, span_exporter
):
    captured: dict[str, Any] = {}

    def fake_run_query_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Runs as the run_in_executor target, exactly like the real
        # run_query_sync — proves trace_headers arrived as a parameter rather
        # than needing to read the (unavailable) current span itself.
        captured["trace_headers"] = kwargs.get("trace_headers")
        return {"row_count": 0, "duration_ms": 1, "wrote_result": False}

    monkeypatch.setattr(supervisor_module, "run_query_sync", fake_run_query_sync)

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("handle_dispatch") as span:
        expected_trace_id = format(span.get_span_context().trace_id, "032x")
        await supervisor_module.run_query(
            "SELECT 1",
            tmp_path / "out.parquet",
            timeout_s=5,
            memory_bytes=1024**3,
            threads=1,
        )

    trace_headers = captured["trace_headers"]
    assert trace_headers is not None
    assert expected_trace_id in trace_headers["traceparent"]

    # No SDK-configured / no-active-span behavior (trace_headers=None reaching
    # run_query_sync) is covered at the run_query_sync/_attach_catalogs layer
    # in test_runner_creds.py — not retested here, since run_query always opens
    # its own duckdb.execute span once an SDK is installed for the process, and
    # trace.set_tracer_provider is process-global (can't be un-configured
    # between tests in this session).


# ── a worker that ignores interrupt() must be abandoned, not awaited forever ──
#
# DuckDB honours interrupt() while processing tuples but not while planning: a
# spinning optimizer holds the executor thread (and the connection) forever,
# with conn.interrupt() doing nothing. Confirmed live via a faulthandler dump
# during a real hang: the worker was stuck inside `conn.sql(sql)` (planning),
# never reaching execution, while every other thread sat idle. Without a bound
# on the executor await itself, that one statement holds its caller (and, for
# a session statement, the session's lock) forever.


async def test_run_query_abandons_a_worker_that_ignores_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def stuck_run_query_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Simulates a spinning planner: interrupt() (called by supervisor's
        # own timeout callback) does nothing to a real blocking sleep either.
        time.sleep(2)
        return {"row_count": 0, "duration_ms": 1, "wrote_result": False}

    monkeypatch.setattr(supervisor_module, "run_query_sync", stuck_run_query_sync)

    start = time.monotonic()
    with pytest.raises(StatementAbandoned):
        await supervisor_module.run_query(
            "SELECT 1", tmp_path / "out.parquet", timeout_s=0.1, memory_bytes=1024**3, threads=1
        )
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"did not abandon promptly at the timeout ({elapsed:.2f}s)"


async def test_run_statement_abandons_a_worker_that_ignores_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def stuck_run_statement_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        time.sleep(2)
        return {"row_count": 0, "duration_ms": 1, "wrote_result": False}

    monkeypatch.setattr(supervisor_module, "run_statement_sync", stuck_run_statement_sync)

    start = time.monotonic()
    with pytest.raises(StatementAbandoned):
        await supervisor_module.run_statement(
            "SELECT 1",
            tmp_path / "out.parquet",
            timeout_s=0.1,
            conn=object(),
            memory_bytes=1024**3,
            threads=1,
        )
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"did not abandon promptly at the timeout ({elapsed:.2f}s)"


async def test_a_cleanly_interrupted_statement_is_a_plain_timeout_not_abandoned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When interrupt() actually works (the tuple-processing case), the worker
    returns promptly with `duckdb.InterruptException` -- that must stay a plain
    `TimeoutError`, not `StatementAbandoned`, since the connection really did
    stop and is safe to reuse."""

    def interrupted_run_statement_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise duckdb.InterruptException("interrupted")

    monkeypatch.setattr(supervisor_module, "run_statement_sync", interrupted_run_statement_sync)

    with pytest.raises(TimeoutError) as exc_info:
        await supervisor_module.run_statement(
            "SELECT 1",
            tmp_path / "out.parquet",
            timeout_s=5,
            conn=object(),
            memory_bytes=1024**3,
            threads=1,
        )
    assert not isinstance(exc_info.value, StatementAbandoned)
