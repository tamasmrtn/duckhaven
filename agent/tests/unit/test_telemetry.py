"""setup_telemetry: disabled by default, installs a provider when an endpoint is set."""

from agent import telemetry
from agent.config import settings


def test_disabled_without_endpoint():
    assert settings.otel_exporter_otlp_endpoint is None
    assert telemetry.setup_telemetry() is False


def test_enabled_sets_provider_and_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", "http://collector:4318")
    try:
        assert telemetry.setup_telemetry() is True
        # Second call is a no-op regardless of settings.
        assert telemetry.setup_telemetry() is False
    finally:
        telemetry._configured = False


def test_instrument_asgi_app_is_noop_when_disabled():
    sentinel = object()
    assert settings.otel_exporter_otlp_endpoint is None
    assert telemetry.instrument_asgi_app(sentinel) is sentinel


def test_instrument_asgi_app_wraps_when_enabled(monkeypatch):
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", "http://collector:4318")
    wrapped = telemetry.instrument_asgi_app(object())
    assert isinstance(wrapped, OpenTelemetryMiddleware)
