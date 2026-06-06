import uuid

from api.services.agent_registry import ConnectionManager


def test_registry_register_and_get():
    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    fake_ws = object()
    mgr.register(agent_id, fake_ws)  # type: ignore[arg-type]
    assert mgr.get(agent_id) is fake_ws


def test_registry_unregister():
    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    mgr.register(agent_id, object())  # type: ignore[arg-type]
    mgr.unregister(agent_id)
    assert mgr.get(agent_id) is None


def test_registry_connected_ids():
    mgr = ConnectionManager()
    a1 = uuid.uuid4()
    a2 = uuid.uuid4()
    mgr.register(a1, object())  # type: ignore[arg-type]
    mgr.register(a2, object())  # type: ignore[arg-type]
    ids = mgr.connected_ids()
    assert str(a1) in ids and str(a2) in ids


def test_registry_records_metrics():
    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    mgr.register(agent_id, object())  # type: ignore[arg-type]
    sample = {"cpu_percent": 12.5, "memory_percent": 40.0, "sampled_at": "2026-06-05T00:00:00Z"}
    mgr.record_metrics(agent_id, sample)
    assert mgr.recent_metrics()[str(agent_id)] == [sample]


def test_registry_metrics_for_unknown_agent_is_dropped():
    mgr = ConnectionManager()
    mgr.record_metrics(uuid.uuid4(), {"cpu_percent": 1.0})
    assert mgr.recent_metrics() == {}


def test_registry_metrics_ring_buffer_bounded():
    from api.services.agent_registry import _METRICS_WINDOW

    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    mgr.register(agent_id, object())  # type: ignore[arg-type]
    for i in range(_METRICS_WINDOW + 50):
        mgr.record_metrics(agent_id, {"cpu_percent": float(i)})
    samples = mgr.recent_metrics()[str(agent_id)]
    assert len(samples) == _METRICS_WINDOW
    # Oldest evicted; newest retained.
    assert samples[-1]["cpu_percent"] == float(_METRICS_WINDOW + 49)
