"""OpenTelemetry tracing setup for the agent.

Disabled unless OTEL_EXPORTER_OTLP_ENDPOINT is set. The agent creates manual spans
(dispatch handling, DuckDB execution) plus one ASGI server span on the results
server (see instrument_asgi_app) — no other auto-instrumentation.
"""

from agent.config import settings

_configured = False


def setup_telemetry() -> bool:
    """Install the OTel SDK. Idempotent; no-op without an endpoint."""
    global _configured
    if _configured or not settings.otel_exporter_otlp_endpoint:
        return False

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_INSTANCE_ID, SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    resource_attrs = {SERVICE_NAME: settings.otel_service_name}
    if settings.agent_name:
        resource_attrs[SERVICE_INSTANCE_ID] = settings.agent_name
    provider = TracerProvider(resource=Resource.create(resource_attrs))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True
    return True


def instrument_asgi_app(app):
    """Wrap an ASGI app in an OTel server-span middleware. No-op if tracing is off.

    The results server receives traceparent-carrying requests from the api's
    (globally instrumented) httpx client, so this server span continues that trace
    instead of ending it at the socket. Uses the provider setup_telemetry installed.
    """
    if not settings.otel_exporter_otlp_endpoint:
        return app
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

    return OpenTelemetryMiddleware(app)
