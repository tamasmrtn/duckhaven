import asyncio
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


# ── Concurrent send safety (#156) ─────────────────────────────────────────────
# One websocket per agent is shared by every concurrent request handler that
# dispatches to it. Sends must be serialized: unsynchronized concurrent writes to
# that shared socket were a prime suspect for frames vanishing between the API and
# the agent.


class _RecordingWS:
    """A socket that yields mid-send, so an unsynchronized caller interleaves."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.events.append(f"enter:{payload}")
        await asyncio.sleep(0)  # a real send awaits the transport here
        self.events.append(f"exit:{payload}")


class _BrokenWS:
    async def send_text(self, payload: str) -> None:
        raise RuntimeError("socket is gone")


async def test_concurrent_sends_do_not_interleave():
    """Two handlers sending at once must produce two whole frames on the wire, not
    two half-frames spliced together."""
    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    ws = _RecordingWS()
    mgr.register(agent_id, ws)  # type: ignore[arg-type]

    await asyncio.gather(*(mgr.send(agent_id, f"frame-{i}") for i in range(5)))

    assert len(ws.events) == 10
    # Every enter is immediately followed by its own exit.
    for i in range(0, len(ws.events), 2):
        enter, exit_ = ws.events[i], ws.events[i + 1]
        assert enter.startswith("enter:")
        assert exit_ == enter.replace("enter:", "exit:", 1), f"interleaved: {ws.events}"


async def test_send_delivers_and_reports_success():
    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    ws = _RecordingWS()
    mgr.register(agent_id, ws)  # type: ignore[arg-type]

    assert await mgr.send(agent_id, "hello") is True
    assert "enter:hello" in ws.events


async def test_send_to_unknown_agent_returns_false():
    mgr = ConnectionManager()
    assert await mgr.send(uuid.uuid4(), "hello") is False


async def test_failed_send_unregisters_the_agent():
    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    mgr.register(agent_id, _BrokenWS())  # type: ignore[arg-type]

    assert await mgr.send(agent_id, "hello") is False
    assert str(agent_id) not in mgr.connected_ids()


async def test_send_lock_is_released_after_a_failed_send():
    """A raising send must not strand the lock, or the agent wedges permanently."""
    mgr = ConnectionManager()
    agent_id = uuid.uuid4()
    mgr.register(agent_id, _BrokenWS())  # type: ignore[arg-type]
    conn = mgr._connections[str(agent_id)]

    assert await mgr.send(agent_id, "hello") is False
    assert not conn.send_lock.locked()
