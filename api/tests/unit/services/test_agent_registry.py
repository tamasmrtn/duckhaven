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
