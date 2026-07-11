"""Trace-context propagation helpers shared by the api and agent.

Pure opentelemetry-api: everything here is safe (and a no-op) when the calling
process never configured an OTel SDK.
"""

from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_propagator = TraceContextTextMapPropagator()


def inject_trace_context(context: Context | None = None) -> dict[str, str] | None:
    """W3C carrier dict for the active (or given) span context; None without one."""
    carrier: dict[str, str] = {}
    _propagator.inject(carrier, context=context)
    return carrier or None


def extract_trace_context(carrier: dict[str, str] | None) -> Context | None:
    """Context extracted from a Frame's trace_context carrier; None when absent."""
    if not carrier:
        return None
    return _propagator.extract(carrier)
