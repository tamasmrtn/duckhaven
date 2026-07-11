"""OpenTelemetry tracing setup for the API.

Disabled unless OTEL_EXPORTER_OTLP_ENDPOINT is set, so local dev and tests run
with zero tracing overhead and no exporter noise. Prometheus metrics
(api.metrics) are a separate, untouched system.
"""

from fastapi import FastAPI

from api.config import settings

_configured = False


def setup_telemetry(*apps: FastAPI) -> bool:
    """Install the OTel SDK and auto-instrumentation. Idempotent.

    Returns True when tracing was configured, False when disabled (no
    endpoint) or already configured. OTel imports stay inside the function so
    the disabled path never pays for them.
    """
    global _configured
    if _configured or not settings.otel_exporter_otlp_endpoint:
        return False

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import SERVICE_INSTANCE_ID, SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: settings.otel_service_name,
                SERVICE_INSTANCE_ID: settings.replica_id,
            }
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # Health and metrics scrapes would dominate span volume; exclude them.
    for app in apps:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    # Covers the shared PolarisClient session and the per-request clients used
    # for cross-replica forwarding; adds W3C traceparent headers to both.
    HTTPXClientInstrumentor().instrument()

    from api.db.session import engine

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    _configured = True
    return True
