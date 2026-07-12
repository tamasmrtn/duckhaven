"""Frame.trace_context round-trips and stays compatible with old peers."""

from duckhaven_shared.protocol import Frame, FrameType


def test_trace_context_round_trips():
    frame = Frame(
        type=FrameType.DISPATCH_QUERY,
        payload={"query_id": "abc"},
        trace_context={"traceparent": "00-aaaa-bbbb-01"},
    )
    parsed = Frame.model_validate_json(frame.model_dump_json())
    assert parsed.trace_context == {"traceparent": "00-aaaa-bbbb-01"}


def test_legacy_json_without_trace_context_defaults_to_none():
    # A frame from a pre-tracing peer never had this field.
    legacy_json = '{"type": "dispatch_query", "payload": {"query_id": "abc"}}'
    parsed = Frame.model_validate_json(legacy_json)
    assert parsed.trace_context is None


def test_unknown_extra_field_is_ignored():
    # A frame from a NEWER peer with a field this version doesn't know about
    # must still parse (pydantic's default extra="ignore").
    future_json = '{"type": "heartbeat", "payload": {}, "some_future_field": 1}'
    parsed = Frame.model_validate_json(future_json)
    assert parsed.type == FrameType.HEARTBEAT
