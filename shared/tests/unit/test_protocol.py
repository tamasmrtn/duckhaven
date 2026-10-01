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


def test_session_frame_types_round_trip():
    # Every SQL-session frame type serializes to its wire string and back.
    for frame_type in (
        FrameType.OPEN_SESSION,
        FrameType.SESSION_OPENED,
        FrameType.EXEC_STATEMENT,
        FrameType.CLOSE_SESSION,
        FrameType.SESSION_CLOSED,
    ):
        frame = Frame(type=frame_type, payload={"session_id": "s1"})
        parsed = Frame.model_validate_json(frame.model_dump_json())
        assert parsed.type == frame_type
        assert parsed.payload == {"session_id": "s1"}


def test_statement_ack_round_trips():
    frame = Frame(type=FrameType.STATEMENT_ACK, payload={"query_id": "abc"})
    parsed = Frame.model_validate_json(frame.model_dump_json())
    assert parsed.type == FrameType.STATEMENT_ACK
    assert parsed.payload["query_id"] == "abc"


def test_agent_capabilities_protocol_features_default_empty():
    """An older agent sends no protocol_features. The API reads its absence as
    "does not support it", which is what keeps the ack deadline from failing every
    statement on an agent that predates acks (#156)."""
    from duckhaven_shared.schemas import AgentCapabilities

    legacy = AgentCapabilities.model_validate(
        {"duckdb_version": "1.1.0", "extensions": ["httpfs"], "memory_limit_gb": 8.0, "cores": 4}
    )
    assert legacy.protocol_features == []


def test_an_older_agents_metrics_sample_parses_with_new_fields_unmeasured():
    """Fields newer agents measure default to None — "not measured" — never to zero,
    so an older agent can't read as having had no memory spike or no OOM kill."""
    from duckhaven_shared.schemas import MetricsSample

    sample = MetricsSample.model_validate(
        {"cpu_percent": 12.5, "memory_percent": 40.0, "sampled_at": "2026-09-30T07:00:00Z"}
    )
    assert sample.memory_peak_percent is None
    assert sample.executing_queries is None
    assert sample.idle_sessions is None
    assert sample.oom_kills is None
    assert sample.interval_s is None
    assert sample.cpu_seconds_total is None
