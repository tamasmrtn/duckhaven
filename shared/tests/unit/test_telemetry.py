"""inject/extract_trace_context: pure opentelemetry-api, safe with no SDK configured."""

from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

from duckhaven_shared.telemetry import extract_trace_context, inject_trace_context


def _remote_context():
    span_context = SpanContext(
        trace_id=0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,
        span_id=0xBBBBBBBBBBBBBBBB,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return set_span_in_context(NonRecordingSpan(span_context))


def test_inject_returns_none_without_active_span():
    # No SDK configured in this test process -> no valid current span.
    assert inject_trace_context() is None


def test_inject_yields_a_traceparent_for_an_explicit_context():
    carrier = inject_trace_context(_remote_context())
    assert carrier is not None
    assert carrier["traceparent"] == "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"


def test_extract_round_trips_an_injected_carrier():
    carrier = inject_trace_context(_remote_context())
    ctx = extract_trace_context(carrier)
    span_context = trace.get_current_span(ctx).get_span_context()
    assert format(span_context.trace_id, "032x") == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert format(span_context.span_id, "016x") == "bbbbbbbbbbbbbbbb"


def test_extract_none_or_empty_carrier_returns_none():
    assert extract_trace_context(None) is None
    assert extract_trace_context({}) is None
