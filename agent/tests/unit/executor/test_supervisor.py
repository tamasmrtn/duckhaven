"""supervisor.run_query's trace-context handoff across run_in_executor.

`loop.run_in_executor` does not propagate contextvars to the worker thread, so
`trace.get_current_span()` returns nothing there. run_query must capture the
active span's W3C carrier on the event-loop thread and pass it into
run_query_sync explicitly, not rely on the worker thread seeing it itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace

from agent.executor import supervisor as supervisor_module


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
