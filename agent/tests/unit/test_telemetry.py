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
