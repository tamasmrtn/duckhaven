"""Trace-context propagation and log correlation shared by the api and agent.

Pure opentelemetry-api: everything here is safe (and a no-op) when the calling
process never configured an OTel SDK.
"""

import logging

from opentelemetry import trace
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


# Log/trace correlation. A fixed placeholder ("-") outside a span keeps the log
# shape grep-stable, rather than switching format strings conditionally.
LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s [trace_id=%(trace_id)s span_id=%(span_id)s] %(message)s"
)


class TraceContextLogFilter(logging.Filter):
    """Stamps the active span's trace_id/span_id (hex) onto every log record.

    Safe with no OTel SDK configured: get_current_span() then returns the
    global no-op span, whose context is invalid, so records get "-" instead.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            record.trace_id = format(span_context.trace_id, "032x")
            record.span_id = format(span_context.span_id, "016x")
        else:
            record.trace_id = "-"
            record.span_id = "-"
        return True


def install_log_correlation() -> None:
    """Attach the trace-correlation filter to the root logger's handlers.

    logging.basicConfig has no filter= parameter, so this runs right after it;
    a handler-level filter stamps every record the handler emits, including
    ones from third-party loggers.
    """
    filt = TraceContextLogFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(filt)
