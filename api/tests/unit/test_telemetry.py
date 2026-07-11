"""setup_telemetry: disabled by default, real spans when an endpoint is set."""

import fastapi

from api import telemetry
from api.config import settings


def test_disabled_without_endpoint():
    # Unit-test env never sets OTEL_EXPORTER_OTLP_ENDPOINT, so the default
    # settings must leave tracing off.
    assert settings.otel_exporter_otlp_endpoint is None
    assert telemetry.setup_telemetry(fastapi.FastAPI()) is False


def test_enabled_instruments_apps_and_is_idempotent(monkeypatch, tracer_provider):
    # tracer_provider is requested first so setup_telemetry's own
    # set_tracer_provider call is the ignored duplicate, not the test's.
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", "http://collector:4318")
    app = fastapi.FastAPI()
    try:
        assert telemetry.setup_telemetry(app) is True
        # FastAPIInstrumentor marks the app it instrumented.
        assert getattr(app, "_is_instrumented_by_opentelemetry", False)
        # Second call is a no-op regardless of settings.
        assert telemetry.setup_telemetry(app) is False
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        HTTPXClientInstrumentor().uninstrument()
        SQLAlchemyInstrumentor().uninstrument()
        telemetry._configured = False


def test_spans_are_recorded_when_enabled(span_exporter):
    """Smoke test: a request through an instrumented app produces a server span."""
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    app = fastapi.FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    try:
        client = TestClient(app)
        assert client.get("/ping").status_code == 200
        names = [s.name for s in span_exporter.get_finished_spans()]
        assert any("/ping" in n for n in names)
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
