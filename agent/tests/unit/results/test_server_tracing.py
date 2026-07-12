"""The instrumented results server emits a server span, plus a duckdb.slice_parquet
child span on a windowed fetch. The in-memory span_exporter fixture's provider is
what the ASGI middleware and the module tracer resolve to."""

import uuid

import duckdb
import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace

from agent import telemetry
from agent.auth import TokenHolder
from agent.config import settings
from agent.results.server import make_results_app

TOKEN = "test-session-token"


def _write_result(results_dir, n: int) -> uuid.UUID:
    query_id = uuid.uuid4()
    path = results_dir / f"{query_id}.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT i AS n FROM range({n}) t(i)) TO '{path}' (FORMAT PARQUET)"
    )
    return query_id


@pytest.fixture
def instrumented_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", "http://collector:4318")
    app = telemetry.instrument_asgi_app(make_results_app(tmp_path, TokenHolder(TOKEN)))
    transport = ASGITransport(app=app)

    async def _make():
        return AsyncClient(transport=transport, base_url="http://test"), tmp_path

    return _make


async def test_full_fetch_emits_server_span_only(instrumented_client, span_exporter):
    client, results_dir = await instrumented_client()
    query_id = _write_result(results_dir, 100)
    async with client as c:
        resp = await c.get(
            f"/results/{query_id}.parquet", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert resp.status_code == 200

    spans = span_exporter.get_finished_spans()
    assert any(s.kind == trace.SpanKind.SERVER for s in spans)
    assert not any(s.name == "duckdb.slice_parquet" for s in spans)


async def test_windowed_fetch_adds_slice_span(instrumented_client, span_exporter):
    client, results_dir = await instrumented_client()
    query_id = _write_result(results_dir, 1000)
    async with client as c:
        resp = await c.get(
            f"/results/{query_id}.parquet?row_offset=100&row_limit=10",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 200

    spans = span_exporter.get_finished_spans()
    slice_spans = [s for s in spans if s.name == "duckdb.slice_parquet"]
    assert len(slice_spans) == 1
    assert slice_spans[0].attributes["db.system.name"] == "duckdb"
    # It nests under the request's server span (same trace).
    server = next(s for s in spans if s.kind == trace.SpanKind.SERVER)
    assert slice_spans[0].context.trace_id == server.context.trace_id
