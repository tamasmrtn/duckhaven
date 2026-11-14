"""DISPATCH_BATCH on the agent: one reservation and one connection run a batch of
reads in order, each reporting its own QUERY_PROGRESS and QUERY_DONE."""

import asyncio

import pytest

import agent.control.channel as ch
from agent.control import session
from agent.executor.admission import Admission
from agent.executor.supervisor import StatementAbandoned
from duckhaven_shared.protocol import Frame, FrameType


def _admission(profile: str = "single", **kwargs) -> Admission:
    kwargs.setdefault("pids_provider", lambda: None)
    return Admission(
        profile=profile,
        headroom=0.0,
        mem_bytes_provider=lambda: 1024**3,
        cores_provider=lambda: 2,
        **kwargs,
    )


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[Frame] = []

    async def send(self, msg: str) -> None:
        self.sent.append(Frame.model_validate_json(msg))

    def outcomes(self) -> dict[str, tuple]:
        return {
            f.payload["query_id"]: (f.payload["status"], f.payload.get("error"))
            for f in self.sent
            if f.type == FrameType.QUERY_DONE
        }


@pytest.fixture(autouse=True)
def _clean():
    session._sessions.clear()
    ch._batch_pending.clear()
    ch._in_flight.clear()
    yield
    session._sessions.clear()
    ch._batch_pending.clear()
    ch._in_flight.clear()


def _payload(*queries: tuple[str, str]) -> dict:
    return {
        "batch_id": "b1",
        "timeout_s": 30,
        "queries": [{"query_id": qid, "sql": sql} for qid, sql in queries],
    }


async def _run(ws, payload, tmp_path, admission, *, cancelled=()):
    """Drive the handler as `_consume` does: the ids are pending before it runs."""
    ch._batch_pending.update(ch._batch_ids(payload))
    ch._batch_pending.difference_update(cancelled)
    await asyncio.wait_for(ch._handle_dispatch_batch(ws, payload, tmp_path, admission), 10)


@pytest.mark.parametrize("profile", ["single", "auto"])
async def test_queries_run_in_order_on_one_connection(tmp_path, monkeypatch, profile):
    opened = []
    real_open = ch._open_connection

    async def counting_open(*args, **kwargs):
        opened.append(kwargs)
        return await real_open(*args, **kwargs)

    monkeypatch.setattr(ch, "_open_connection", counting_open)
    admission = _admission(profile)
    ws = _FakeWS()

    await _run(ws, _payload(("q1", "SELECT 1 AS n"), ("q2", "SELECT 2 AS n")), tmp_path, admission)

    assert [(f.type, f.payload["query_id"]) for f in ws.sent] == [
        (FrameType.QUERY_PROGRESS, "q1"),
        (FrameType.QUERY_DONE, "q1"),
        (FrameType.QUERY_PROGRESS, "q2"),
        (FrameType.QUERY_DONE, "q2"),
    ]
    assert ws.outcomes() == {"q1": ("done", None), "q2": ("done", None)}
    assert len(opened) == 1
    assert (tmp_path / "q1.parquet").exists() and (tmp_path / "q2.parquet").exists()
    # Small results travel in the frame, as a session statement's do.
    assert all("result_parquet" in f.payload for f in ws.sent if f.type == FrameType.QUERY_DONE)
    # Everything is handed back.
    assert admission.running_count == 0
    assert session.count() == 0
    assert not ch._batch_pending and not ch._in_flight


async def test_a_failing_query_fails_alone(tmp_path):
    ws = _FakeWS()
    payload = _payload(("q1", "SELECT 1"), ("q2", "SELECT * FROM missing"), ("q3", "SELECT 3"))

    await _run(ws, payload, tmp_path, _admission())

    outcomes = ws.outcomes()
    assert outcomes["q1"] == ("done", None)
    assert outcomes["q2"][0] == "failed" and "missing" in outcomes["q2"][1]
    assert outcomes["q3"] == ("done", None)


async def test_a_query_cancelled_before_its_turn_is_skipped(tmp_path):
    ws = _FakeWS()
    payload = _payload(("q1", "SELECT 1"), ("q2", "SELECT 2"), ("q3", "SELECT 3"))

    await _run(ws, payload, tmp_path, _admission(), cancelled={"q2"})

    # Nothing at all for q2: the control plane already marked it cancelled.
    assert {f.payload["query_id"] for f in ws.sent} == {"q1", "q3"}


async def test_cancelling_the_running_query_moves_on_to_the_next(tmp_path):
    admission = _admission()
    ws = _FakeWS()
    payload = _payload(("slow", "SELECT count(*) FROM range(10000000000000)"), ("q2", "SELECT 2"))
    ch._batch_pending.update(ch._batch_ids(payload))
    batch = asyncio.create_task(ch._handle_dispatch_batch(ws, payload, tmp_path, admission))
    async with asyncio.timeout(10):
        while not any(f.type == FrameType.QUERY_PROGRESS for f in ws.sent):
            await asyncio.sleep(0.01)

    # What `_consume` does with a CANCEL_QUERY.
    ch._batch_pending.discard("slow")
    ch._in_flight.pop("slow").cancel()
    await asyncio.wait_for(batch, 10)

    assert ws.outcomes() == {"q2": ("done", None)}
    assert admission.running_count == 0


async def test_an_abandoned_statement_aborts_the_rest(tmp_path, monkeypatch):
    real_run = ch.run_statement
    discarded = []
    real_discard = session.discard_poisoned

    async def run(sql, *args, **kwargs):
        if sql == "SELECT 2":
            raise StatementAbandoned("statement exceeded timeout")
        return await real_run(sql, *args, **kwargs)

    async def spy_discard(session_id, admission):
        discarded.append(session_id)
        return await real_discard(session_id, admission)

    monkeypatch.setattr(ch, "run_statement", run)
    monkeypatch.setattr(session, "discard_poisoned", spy_discard)
    admission = _admission(profile="auto")
    ws = _FakeWS()
    payload = _payload(
        ("q1", "SELECT 1"), ("q2", "SELECT 2"), ("q3", "SELECT 3"), ("q4", "SELECT 4")
    )

    await _run(ws, payload, tmp_path, admission, cancelled={"q4"})

    assert ws.outcomes() == {
        "q1": ("done", None),
        "q2": ("failed", "statement exceeded timeout"),
        "q3": ("failed", "batch_aborted"),
    }
    # Dropped without touching the connection, and the reservation released.
    assert discarded == ["batch:b1"]
    assert admission.running_count == 0
    assert session.count() == 0


async def test_a_failed_open_fails_every_query_and_releases(tmp_path, monkeypatch):
    async def broken_open(*args, **kwargs):
        raise RuntimeError("attach failed")

    monkeypatch.setattr(ch, "_open_connection", broken_open)
    admission = _admission()
    ws = _FakeWS()

    await _run(ws, _payload(("q1", "SELECT 1"), ("q2", "SELECT 2")), tmp_path, admission)

    assert ws.outcomes() == {"q1": ("failed", "attach failed"), "q2": ("failed", "attach failed")}
    assert admission.running_count == 0


async def test_a_full_queue_fails_the_batch(tmp_path):
    admission = _admission(profile="single", max_queue_depth=0)
    held = await admission.acquire()
    ws = _FakeWS()

    await _run(ws, _payload(("q1", "SELECT 1"), ("q2", "SELECT 2")), tmp_path, admission)

    assert ws.outcomes() == {"q1": ("failed", "queue full"), "q2": ("failed", "queue full")}
    admission.release(held)
    assert admission.running_count == 0


async def test_the_lease_sweep_leaves_a_batch_alone(tmp_path, monkeypatch):
    """A batch between two queries holds no lock, and must not be reaped."""
    admission = _admission()
    swept = []
    real_run_on_state = ch._run_on_state

    async def run_on_state(ws, state, *args, **kwargs):
        swept.extend(await session.sweep_expired(admission, idle_s=1e-9, max_life_s=1e-9))
        return await real_run_on_state(ws, state, *args, **kwargs)

    monkeypatch.setattr(ch, "_run_on_state", run_on_state)
    ws = _FakeWS()

    await _run(ws, _payload(("q1", "SELECT 1"), ("q2", "SELECT 2")), tmp_path, admission)

    assert swept == []
    assert ws.outcomes() == {"q1": ("done", None), "q2": ("done", None)}


class _IterWS(_FakeWS):
    def __init__(self, msgs: list[str]) -> None:
        super().__init__()
        self._msgs = msgs

    async def __aiter__(self):
        for msg in self._msgs:
            yield msg


async def test_a_cancel_read_straight_after_the_batch_skips_its_query(tmp_path):
    """The ids are pending as soon as the frame is read, before the batch runs."""
    payload = _payload(("q1", "SELECT 1"), ("q2", "SELECT 2"))
    ws = _IterWS(
        [
            Frame(type=FrameType.DISPATCH_BATCH, payload=payload).model_dump_json(),
            Frame(type=FrameType.CANCEL_QUERY, payload={"query_id": "q1"}).model_dump_json(),
        ]
    )

    await ch._consume(ws, tmp_path, _admission())
    async with asyncio.timeout(10):
        while "q2" not in ws.outcomes():
            await asyncio.sleep(0.01)

    assert ws.outcomes() == {"q2": ("done", None)}


def test_capabilities_advertise_batch_dispatch():
    """The control plane sends DISPATCH_BATCH only to agents that say so."""
    assert "batch_dispatch" in ch._PROTOCOL_FEATURES
