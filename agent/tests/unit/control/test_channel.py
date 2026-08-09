import asyncio
import contextlib
import threading
import uuid

import pytest
import websockets
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

from agent.control import session
from agent.executor.admission import Admission, ReservationRequest
from duckhaven_shared.concurrency import BUCKET_FRACTIONS
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.schemas import AgentCapabilities
from duckhaven_shared.telemetry import inject_trace_context


@pytest.fixture(autouse=True)
def _clear_sessions():
    # The session registry is process-global; keep tests isolated (mirrors the
    # fixture in tests/unit/control/test_session.py). The in-flight-open registry
    # in channel is process-global for the same reason and needs the same care.
    import agent.control.channel as ch_module

    session._sessions.clear()
    ch_module._opening.clear()
    ch_module._abandoned.clear()
    yield
    session._sessions.clear()
    ch_module._opening.clear()
    ch_module._abandoned.clear()


def _admission(profile: str = "single", **kwargs) -> Admission:
    """A test admission manager with an exact, headroom-free budget."""
    return Admission(
        profile=profile,
        headroom=0.0,
        mem_bytes_provider=lambda: 1024**3,
        cores_provider=lambda: 2,
        **kwargs,
    )


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, msg: str) -> None:
        self.sent.append(msg)


def _frame_types(ws: _FakeWS) -> list[FrameType]:
    return [Frame.model_validate_json(m).type for m in ws.sent]


async def _complete_auth(ws, *, session_token: str = "tok-test") -> None:
    """Server-side helper: complete AUTH → AUTH_OK → AGENT_STATUS handshake."""
    raw = await ws.recv()
    Frame.model_validate_json(raw)
    await ws.send(
        Frame(
            type=FrameType.AUTH_OK,
            payload={"agent_id": str(uuid.uuid4()), "session_token": session_token},
        ).model_dump_json()
    )
    await ws.recv()  # consume AGENT_STATUS


def test_get_capabilities_loads_and_advertises_query_extensions(monkeypatch):
    """The agent must LOAD the pre-installed query extensions before
    introspecting, so httpfs/azure/iceberg are advertised — dispatch is gated on
    them (regression for the agent-incompatible defect)."""
    import duckdb

    import agent.control.channel as ch_module

    loaded: list[str] = []

    class FakeConn:
        def execute(self, sql: str):
            if sql.startswith("LOAD "):
                loaded.append(sql.split(" ", 1)[1])
                return self

            # Introspection query returns whatever was LOADed plus a built-in.
            class _Cur:
                def fetchall(_self):
                    return [("parquet",), *[(ext,) for ext in loaded]]

            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(duckdb, "version", lambda: "v-test")

    caps = ch_module._get_capabilities()

    assert loaded == ["httpfs", "azure", "iceberg"]
    assert "httpfs" in caps.extensions
    assert "iceberg" in caps.extensions


def test_get_capabilities_reports_detected_cpu(monkeypatch):
    """`cores` is no longer hardcoded — it (and the CPU model) come from runtime
    detection rather than a fixed `1`."""
    import duckdb

    import agent.control.channel as ch_module

    class FakeConn:
        def execute(self, sql: str):
            if "duckdb_extensions" not in sql:
                return self  # LOAD <ext>

            class _Cur:
                def fetchall(_self):
                    return [("parquet",)]

            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(duckdb, "version", lambda: "v-test")
    monkeypatch.setattr(
        ch_module,
        "cpu_capability",
        lambda: {"cores": 4, "cpu_model": "Test CPU", "cpu_cores_physical": 2},
    )

    caps = ch_module._get_capabilities()

    assert caps.cores == 4
    assert caps.cpu_model == "Test CPU"
    assert caps.cpu_cores_physical == 2


async def test_pushes_metrics_samples(tmp_path, monkeypatch):
    """After auth the agent streams METRICS_SAMPLE frames on its own cadence."""
    import agent.control.channel as ch_module

    # Stub capability introspection: it runs synchronously in the startup path,
    # *before* the metrics task is created, so its cost sits inside this test's
    # deadline below. The real one opens DuckDB and LOADs httpfs/azure/iceberg —
    # and since `autoinstall_known_extensions` is on by default, a machine with a
    # cold extension cache downloads all three over the network first. This is the
    # first test in the file to reach the real `_get_capabilities` (the two tests
    # that cover it directly fake duckdb, and every later channel test finds the
    # cache warm), so it alone paid that cost and timed out on CI while passing
    # locally. What capabilities report is asserted at
    # `test_get_capabilities_loads_and_advertises_query_extensions`; this test is
    # about the metrics cadence.
    monkeypatch.setattr(
        ch_module,
        "_get_capabilities",
        lambda: AgentCapabilities(
            duckdb_version="1.5.4", extensions=["httpfs"], memory_limit_gb=1.0, cores=2
        ),
    )
    monkeypatch.setattr(ch_module.settings, "metrics_sample_interval_s", 0.05)
    got = asyncio.Event()
    received: list[Frame] = []

    async def handler(ws):
        await _complete_auth(ws)
        for _ in range(10):
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            frame = Frame.model_validate_json(raw)
            if frame.type == FrameType.METRICS_SAMPLE:
                received.append(frame)
                got.set()
                break
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.wait_for(got.wait(), timeout=3.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert received
    payload = received[0].payload
    assert "cpu_percent" in payload
    assert "memory_percent" in payload
    assert "sampled_at" in payload


async def test_dispatch_clamps_to_operator_ceilings(tmp_path, monkeypatch):
    """A per-query timeout override is clamped to the agent's operator ceiling
    before execution (G-D2-b)."""
    import agent.control.channel as ch_module

    captured: dict[str, float] = {}

    async def fake_run_query(sql, result_path, timeout_s, **kwargs):
        captured["timeout_s"] = timeout_s
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 0, "duration_ms": 0}

    monkeypatch.setattr(ch_module, "run_query", fake_run_query)
    monkeypatch.setattr(ch_module.settings, "max_timeout_s", 60.0)

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, msg: str) -> None:
            self.sent.append(msg)

    ws = FakeWS()
    await ch_module._handle_dispatch(
        ws,
        {
            "query_id": str(uuid.uuid4()),
            "sql": "SELECT 1",
            "timeout_s": 99999.0,
        },
        tmp_path,
        _admission(),
    )

    assert captured["timeout_s"] == 60.0


async def _serve_bootstrap_exchange(websocket, session_token: str = "tok-abc"):
    """Mock control-plane: accept auth, send auth_ok, then accept one more frame."""
    raw = await websocket.recv()
    frame = Frame.model_validate_json(raw)
    assert frame.type == FrameType.AUTH

    auth_ok = Frame(
        type=FrameType.AUTH_OK,
        payload={"agent_id": str(uuid.uuid4()), "session_token": session_token},
    )
    await websocket.send(auth_ok.model_dump_json())

    # Receive capabilities frame
    caps_raw = await websocket.recv()
    caps = Frame.model_validate_json(caps_raw)
    assert caps.type == FrameType.AGENT_STATUS

    # Hold open briefly then close
    await asyncio.sleep(0.05)


async def test_bootstrap_exchange(tmp_path, monkeypatch):
    """Channel completes auth handshake with a mock control plane."""
    import agent.control.channel as ch_module

    received_caps: list[Frame] = []

    async def handler(ws):
        raw = await ws.recv()
        frame = Frame.model_validate_json(raw)
        assert frame.type == FrameType.AUTH
        assert "token" in frame.payload
        # The agent advertises where its result server listens so the control
        # plane can fetch result Parquet (host = socket peer, observed by API).
        assert frame.payload["result_port"] == ch_module.settings.results_http_port

        auth_ok = Frame(
            type=FrameType.AUTH_OK,
            payload={"agent_id": str(uuid.uuid4()), "session_token": "tok-xyz"},
        )
        await ws.send(auth_ok.model_dump_json())

        caps_raw = await ws.recv()
        received_caps.append(Frame.model_validate_json(caps_raw))
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok-boot")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert len(received_caps) == 1
    assert received_caps[0].type == FrameType.AGENT_STATUS


async def test_auth_frame_uses_configured_agent_name(tmp_path, monkeypatch):
    """When AGENT_NAME is set, the agent advertises it as its display name."""
    import agent.control.channel as ch_module

    auth_names: list[str] = []

    async def handler(ws):
        raw = await ws.recv()
        auth_names.append(Frame.model_validate_json(raw).payload["name"])
        await ws.send(
            Frame(
                type=FrameType.AUTH_OK,
                payload={"agent_id": str(uuid.uuid4()), "session_token": "tok"},
            ).model_dump_json()
        )
        await ws.recv()  # capabilities
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok-boot")
        monkeypatch.setattr(ch_module.settings, "agent_name", "analytics-prod-1")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert auth_names[0] == "analytics-prod-1"


async def test_auth_frame_falls_back_to_hostname(tmp_path, monkeypatch):
    """With no AGENT_NAME, the agent advertises its host name (platform.node)."""
    import platform

    import agent.control.channel as ch_module

    auth_names: list[str] = []

    async def handler(ws):
        raw = await ws.recv()
        auth_names.append(Frame.model_validate_json(raw).payload["name"])
        await ws.send(
            Frame(
                type=FrameType.AUTH_OK,
                payload={"agent_id": str(uuid.uuid4()), "session_token": "tok"},
            ).model_dump_json()
        )
        await ws.recv()  # capabilities
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok-boot")
        monkeypatch.setattr(ch_module.settings, "agent_name", "")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert auth_names[0] == platform.node()


async def test_auth_ok_populates_token_holder(tmp_path, monkeypatch):
    """The session token from auth_ok lands in the shared TokenHolder so the
    result server can authenticate control-plane range reads."""
    import agent.control.channel as ch_module
    from agent.auth import TokenHolder

    holder = TokenHolder()

    async def handler(ws):
        await _complete_auth(ws, session_token="tok-holder")
        await asyncio.sleep(0.1)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok-boot")

        task = asyncio.create_task(
            ch_module.run_control_channel(results_dir=tmp_path, token_holder=holder)
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert holder.value == "tok-holder"


async def test_auth_ok_persists_session_token_to_disk(tmp_path, monkeypatch):
    """The session token from auth_ok is written to the session-token file so a
    restart can re-authenticate without the (consumed) bootstrap token."""
    import agent.control.channel as ch_module

    session_path = tmp_path / ".session-token"

    async def handler(ws):
        await _complete_auth(ws, session_token="tok-persisted")
        await asyncio.sleep(0.1)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok-boot")

        task = asyncio.create_task(
            ch_module.run_control_channel(results_dir=tmp_path, session_token_path=session_path)
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert session_path.read_text().strip() == "tok-persisted"


async def test_reconnect_uses_persisted_session_token(tmp_path, monkeypatch):
    """With a persisted session token, the agent authenticates with it rather
    than re-sending the single-use bootstrap token (BUG-2 reconnect path)."""
    import agent.control.channel as ch_module

    session_path = tmp_path / ".session-token"
    session_path.write_text("tok-from-disk")

    sent_tokens: list[str] = []

    async def handler(ws):
        raw = await ws.recv()
        sent_tokens.append(Frame.model_validate_json(raw).payload["token"])
        await ws.send(
            Frame(
                type=FrameType.AUTH_OK,
                payload={"agent_id": str(uuid.uuid4()), "session_token": "tok-from-disk"},
            ).model_dump_json()
        )
        await ws.recv()  # capabilities
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok-boot")

        task = asyncio.create_task(
            ch_module.run_control_channel(results_dir=tmp_path, session_token_path=session_path)
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert sent_tokens[0] == "tok-from-disk"


async def test_first_registration_falls_back_to_bootstrap_token(tmp_path, monkeypatch):
    """With no persisted session token, the agent authenticates with the
    bootstrap token (very first registration)."""
    import agent.control.channel as ch_module

    session_path = tmp_path / ".session-token"  # does not exist
    sent_tokens: list[str] = []

    async def handler(ws):
        raw = await ws.recv()
        sent_tokens.append(Frame.model_validate_json(raw).payload["token"])
        await ws.send(
            Frame(
                type=FrameType.AUTH_OK,
                payload={"agent_id": str(uuid.uuid4()), "session_token": "tok-new"},
            ).model_dump_json()
        )
        await ws.recv()  # capabilities
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok-boot")

        task = asyncio.create_task(
            ch_module.run_control_channel(results_dir=tmp_path, session_token_path=session_path)
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert sent_tokens[0] == "tok-boot"


async def test_dispatch_sends_done_frame(tmp_path, monkeypatch):
    """Dispatch query frame triggers runner and agent sends QUERY_DONE back."""
    import agent.control.channel as ch_module
    import agent.executor.supervisor as sup_module

    query_id = str(uuid.uuid4())
    done_frames: list[Frame] = []

    async def mock_run_query(sql, result_path, timeout_s, **kwargs):
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 1, "duration_ms": 10}

    monkeypatch.setattr(sup_module, "run_query", mock_run_query)

    async def handler(ws):
        raw = await ws.recv()
        Frame.model_validate_json(raw)  # auth frame

        await ws.send(
            Frame(
                type=FrameType.AUTH_OK,
                payload={"agent_id": str(uuid.uuid4()), "session_token": "tok"},
            ).model_dump_json()
        )
        await ws.recv()  # capabilities

        dispatch = Frame(
            type=FrameType.DISPATCH_QUERY,
            payload={
                "query_id": query_id,
                "sql": "SELECT 1",
                "timeout_s": 30.0,
            },
        )
        await ws.send(dispatch.model_dump_json())

        # Collect all frames until done
        for _ in range(5):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                done_frames.append(Frame.model_validate_json(raw))
                if done_frames[-1].type == FrameType.QUERY_DONE:
                    break
            except TimeoutError:
                break

        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "boot")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    done = next((f for f in done_frames if f.type == FrameType.QUERY_DONE), None)
    assert done is not None
    assert done.payload["query_id"] == query_id
    assert done.payload["status"] == "done"


async def test_auth_failure_exits(tmp_path, monkeypatch):
    """Channel returns immediately when the server does not respond with AUTH_OK."""
    import agent.control.channel as ch_module

    error_sent = asyncio.Event()

    async def handler(ws):
        await ws.recv()  # consume AUTH frame
        # Respond with a non-AUTH_OK frame type
        await ws.send(Frame(type=FrameType.AGENT_STATUS, payload={}).model_dump_json())
        error_sent.set()
        await asyncio.sleep(0.5)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await error_sent.wait()
        await asyncio.sleep(0.1)

        assert task.done()


async def test_heartbeat_echo(tmp_path, monkeypatch):
    """Channel echoes HEARTBEAT frames back to the server."""
    import agent.control.channel as ch_module

    heartbeat_received = asyncio.Event()

    async def handler(ws):
        await _complete_auth(ws)
        await ws.send(Frame(type=FrameType.HEARTBEAT).model_dump_json())
        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
        frame = Frame.model_validate_json(raw)
        if frame.type == FrameType.HEARTBEAT:
            heartbeat_received.set()
        await asyncio.sleep(0.1)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await heartbeat_received.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert heartbeat_received.is_set()


async def test_heartbeat_readvertises_capabilities(tmp_path, monkeypatch):
    """On each heartbeat the agent re-sends its capabilities (G-D17-a)."""
    import agent.control.channel as ch_module

    got_status = asyncio.Event()

    async def handler(ws):
        await _complete_auth(ws)  # consumes the connect-time AGENT_STATUS
        await ws.send(Frame(type=FrameType.HEARTBEAT).model_dump_json())
        for _ in range(3):
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            if Frame.model_validate_json(raw).type == FrameType.AGENT_STATUS:
                got_status.set()
                break
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await got_status.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    assert got_status.is_set()


async def test_dispatch_error_sends_failed_frame(tmp_path, monkeypatch):
    """When run_query raises, the channel sends QUERY_DONE with status=failed."""
    import agent.control.channel as ch_module

    query_id = str(uuid.uuid4())
    done_frames: list[Frame] = []

    async def failing_run_query(sql, result_path, timeout_s, **kwargs):
        raise RuntimeError("intentional-error")

    # Patch the name as imported into channel.py, not the supervisor module
    monkeypatch.setattr(ch_module, "run_query", failing_run_query)

    async def handler(ws):
        await _complete_auth(ws)

        await ws.send(
            Frame(
                type=FrameType.DISPATCH_QUERY,
                payload={
                    "query_id": query_id,
                    "sql": "NOT VALID SQL",
                    "timeout_s": 30.0,
                },
            ).model_dump_json()
        )

        for _ in range(5):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                f = Frame.model_validate_json(raw)
                done_frames.append(f)
                if f.type == FrameType.QUERY_DONE:
                    break
            except TimeoutError:
                break

        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    done = next((f for f in done_frames if f.type == FrameType.QUERY_DONE), None)
    assert done is not None
    assert done.payload["status"] == "failed"
    assert "intentional-error" in done.payload["error"]


async def test_cancel_query_cancels_in_flight_task(tmp_path, monkeypatch):
    """CANCEL_QUERY removes the task from _in_flight and cancels it."""
    import agent.control.channel as ch_module
    import agent.executor.supervisor as sup_module

    query_id = str(uuid.uuid4())
    progress_received = asyncio.Event()

    async def slow_run_query(sql, result_path, timeout_s, **kwargs):
        await asyncio.sleep(30)
        return {"row_count": 0, "duration_ms": 0}

    monkeypatch.setattr(sup_module, "run_query", slow_run_query)

    async def handler(ws):
        await _complete_auth(ws)

        await ws.send(
            Frame(
                type=FrameType.DISPATCH_QUERY,
                payload={
                    "query_id": query_id,
                    "sql": "SELECT 1",
                    "timeout_s": 60.0,
                },
            ).model_dump_json()
        )

        # Wait for QUERY_PROGRESS to know the task started
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        progress_received.set()

        await ws.send(
            Frame(
                type=FrameType.CANCEL_QUERY,
                payload={"query_id": query_id},
            ).model_dump_json()
        )

        await asyncio.sleep(0.3)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await progress_received.wait()
        await asyncio.sleep(0.2)

        # After CANCEL_QUERY the task is popped from _in_flight immediately
        assert query_id not in ch_module._in_flight

        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass


async def test_set_concurrency_via_consume_reconfigures(tmp_path):
    """A SET_CONCURRENCY frame retunes the admission manager's active profile."""
    import agent.control.channel as ch_module

    admission = _admission(profile="decaying_3")

    class IterWS:
        def __init__(self, msgs):
            self._msgs = msgs
            self.sent: list[str] = []

        async def send(self, msg):
            self.sent.append(msg)

        async def __aiter__(self):
            for msg in self._msgs:
                yield msg

    ws = IterWS(
        [Frame(type=FrameType.SET_CONCURRENCY, payload={"profile": "single"}).model_dump_json()]
    )
    await ch_module._consume(ws, tmp_path, admission)
    assert admission.active_profile == "single"


async def test_second_query_stays_queued_until_first_finishes(tmp_path, monkeypatch):
    """With one slot, a second dispatch waits in the queue (no QUERY_PROGRESS)
    until the first finishes and frees the slot."""
    import agent.control.channel as ch_module

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run_query(sql, result_path, timeout_s, **kwargs):
        started.set()
        await release.wait()
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 0, "duration_ms": 0}

    monkeypatch.setattr(ch_module, "run_query", fake_run_query)
    admission = _admission(profile="single")

    ws1, ws2 = _FakeWS(), _FakeWS()
    t1 = asyncio.create_task(
        ch_module._handle_dispatch(ws1, {"query_id": "q1", "sql": "S"}, tmp_path, admission)
    )
    await started.wait()
    started.clear()

    t2 = asyncio.create_task(
        ch_module._handle_dispatch(ws2, {"query_id": "q2", "sql": "S"}, tmp_path, admission)
    )
    await asyncio.sleep(0)
    assert admission.queued_count == 1
    assert FrameType.QUERY_PROGRESS not in _frame_types(ws2)  # still queued

    release.set()
    await t1
    await started.wait()  # q2 admitted and now running
    assert FrameType.QUERY_PROGRESS in _frame_types(ws2)
    await t2


async def test_auto_mode_estimates_then_sizes(tmp_path, monkeypatch):
    """In the `auto` profile the channel opens+attaches a connection, estimates
    via EXPLAIN, sizes the reservation from a bucket, and reuses that connection."""
    import agent.control.channel as ch_module

    captured: dict[str, object] = {}

    async def fake_run_query(sql, result_path, timeout_s, **kwargs):
        captured["memory_bytes"] = kwargs["memory_bytes"]
        captured["conn"] = kwargs.get("conn")
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 0, "duration_ms": 0, "wrote_result": True, "profile": None}

    monkeypatch.setattr(ch_module, "run_query", fake_run_query)
    admission = _admission(profile="auto", floor_bytes=1, ceiling_fraction=1.0)

    ws = _FakeWS()
    await ch_module._handle_dispatch(ws, {"query_id": "q", "sql": "SELECT 1"}, tmp_path, admission)

    # The auto path opened a connection (for EXPLAIN) and handed it to the runner.
    assert captured["conn"] is not None
    # SELECT 1 has no blocking operator -> smallest (XS) bucket = budget/12.
    assert captured["memory_bytes"] == int(admission.budget_bytes * (1 / 12))
    done = Frame.model_validate_json(ws.sent[-1])
    assert done.payload["status"] == "done"


async def test_auto_estimate_failure_falls_back_without_dropping(tmp_path, monkeypatch):
    """If estimation fails, the channel uses the fallback bucket and still runs
    the query (never drops it)."""
    import agent.control.channel as ch_module

    def boom(*args, **kwargs):
        raise RuntimeError("estimate exploded")

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", boom)
    monkeypatch.setattr(ch_module.settings, "estimate_fallback_bucket", "M")

    captured: dict[str, object] = {}

    async def fake_run_query(sql, result_path, timeout_s, **kwargs):
        captured["memory_bytes"] = kwargs["memory_bytes"]
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 0, "duration_ms": 0, "wrote_result": True, "profile": None}

    monkeypatch.setattr(ch_module, "run_query", fake_run_query)
    admission = _admission(profile="auto", floor_bytes=1, ceiling_fraction=1.0)

    ws = _FakeWS()
    await ch_module._handle_dispatch(ws, {"query_id": "q", "sql": "SELECT 1"}, tmp_path, admission)

    # Fallback bucket M = budget/3.
    assert captured["memory_bytes"] == int(admission.budget_bytes * (1 / 3))
    done = Frame.model_validate_json(ws.sent[-1])
    assert done.payload["status"] == "done"


async def test_static_profile_opens_no_estimate_connection(tmp_path, monkeypatch):
    """Static profiles are unchanged: no pre-acquire connection; runner opens its own."""
    import agent.control.channel as ch_module

    captured: dict[str, object] = {}

    async def fake_run_query(sql, result_path, timeout_s, **kwargs):
        captured["conn"] = kwargs.get("conn")
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 0, "duration_ms": 0, "wrote_result": True, "profile": None}

    monkeypatch.setattr(ch_module, "run_query", fake_run_query)
    admission = _admission(profile="single")

    ws = _FakeWS()
    await ch_module._handle_dispatch(ws, {"query_id": "q", "sql": "SELECT 1"}, tmp_path, admission)
    assert captured["conn"] is None


async def test_queue_full_returns_failed_frame(tmp_path, monkeypatch):
    """When the slot is busy and the queue is at capacity, a new dispatch fails
    fast with a 'queue full' QUERY_DONE rather than oversubscribing."""
    import agent.control.channel as ch_module

    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_run_query(sql, result_path, timeout_s, **kwargs):
        started.set()
        await release.wait()
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 0, "duration_ms": 0}

    monkeypatch.setattr(ch_module, "run_query", blocking_run_query)
    admission = _admission(profile="single", max_queue_depth=0)

    ws1 = _FakeWS()
    t1 = asyncio.create_task(
        ch_module._handle_dispatch(ws1, {"query_id": "q1", "sql": "S"}, tmp_path, admission)
    )
    await started.wait()

    ws2 = _FakeWS()
    await ch_module._handle_dispatch(ws2, {"query_id": "q2", "sql": "S"}, tmp_path, admission)
    done = Frame.model_validate_json(ws2.sent[-1])
    assert done.type == FrameType.QUERY_DONE
    assert done.payload["status"] == "failed"
    assert done.payload["error"] == "queue full"

    release.set()
    await t1


def _remote_trace_context() -> dict[str, str]:
    span_context = SpanContext(
        trace_id=0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,
        span_id=0xBBBBBBBBBBBBBBBB,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    ctx = set_span_in_context(NonRecordingSpan(span_context))
    return inject_trace_context(ctx)


async def test_dispatch_with_trace_context_parents_the_span(tmp_path, monkeypatch, span_exporter):
    """A DISPATCH_QUERY carrying trace_context produces a handle_dispatch span
    parented to the api's trace, not a new root."""
    import agent.control.channel as ch_module
    import agent.executor.supervisor as sup_module

    query_id = str(uuid.uuid4())

    async def mock_run_query(sql, result_path, timeout_s, **kwargs):
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 1, "duration_ms": 10}

    monkeypatch.setattr(sup_module, "run_query", mock_run_query)

    async def handler(ws):
        await _complete_auth(ws)
        dispatch = Frame(
            type=FrameType.DISPATCH_QUERY,
            payload={"query_id": query_id, "sql": "SELECT 1", "timeout_s": 30.0},
            trace_context=_remote_trace_context(),
        )
        await ws.send(dispatch.model_dump_json())
        for _ in range(5):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                if Frame.model_validate_json(raw).type == FrameType.QUERY_DONE:
                    break
            except TimeoutError:
                break
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "boot")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    spans = [s for s in span_exporter.get_finished_spans() if s.name == "handle_dispatch"]
    assert len(spans) == 1
    span = spans[0]
    assert format(span.context.trace_id, "032x") == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert format(span.parent.span_id, "016x") == "bbbbbbbbbbbbbbbb"
    assert span.attributes["duckhaven.query_id"] == query_id


async def test_dispatch_without_trace_context_creates_root_span(
    tmp_path, monkeypatch, span_exporter
):
    """A DISPATCH_QUERY from an old peer (no trace_context) still executes and
    produces a root span rather than failing."""
    import agent.control.channel as ch_module
    import agent.executor.supervisor as sup_module

    query_id = str(uuid.uuid4())

    async def mock_run_query(sql, result_path, timeout_s, **kwargs):
        result_path.write_bytes(b"PAR1fake")
        return {"row_count": 1, "duration_ms": 10}

    monkeypatch.setattr(sup_module, "run_query", mock_run_query)

    async def handler(ws):
        await _complete_auth(ws)
        # No trace_context passed -> defaults to None, matching a legacy sender.
        dispatch = Frame(
            type=FrameType.DISPATCH_QUERY,
            payload={"query_id": query_id, "sql": "SELECT 1", "timeout_s": 30.0},
        )
        await ws.send(dispatch.model_dump_json())
        for _ in range(5):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                if Frame.model_validate_json(raw).type == FrameType.QUERY_DONE:
                    break
            except TimeoutError:
                break
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "boot")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    spans = [s for s in span_exporter.get_finished_spans() if s.name == "handle_dispatch"]
    assert len(spans) == 1
    assert spans[0].parent is None


async def test_dispatch_failure_sets_span_error_status(tmp_path, monkeypatch, span_exporter):
    """A run_query exception is recorded on the handle_dispatch span as an error."""
    import agent.control.channel as ch_module

    query_id = str(uuid.uuid4())

    async def failing_run_query(sql, result_path, timeout_s, **kwargs):
        raise RuntimeError("intentional-error")

    monkeypatch.setattr(ch_module, "run_query", failing_run_query)

    async def handler(ws):
        await _complete_auth(ws)
        await ws.send(
            Frame(
                type=FrameType.DISPATCH_QUERY,
                payload={"query_id": query_id, "sql": "NOT VALID SQL", "timeout_s": 30.0},
            ).model_dump_json()
        )
        for _ in range(5):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                if Frame.model_validate_json(raw).type == FrameType.QUERY_DONE:
                    break
            except TimeoutError:
                break
        await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(ch_module.settings, "control_plane_url", f"ws://127.0.0.1:{port}")
        monkeypatch.setattr(ch_module.settings, "bootstrap_token", "tok")

        task = asyncio.create_task(ch_module.run_control_channel(results_dir=tmp_path))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError, Exception:
            pass

    spans = [s for s in span_exporter.get_finished_spans() if s.name == "handle_dispatch"]
    assert len(spans) == 1
    assert spans[0].status.status_code == trace.StatusCode.ERROR
    assert len(spans[0].events) == 1  # record_exception


async def test_close_session_dispatch_frees_admission_slot(tmp_path):
    """CLOSE_SESSION dispatch must hand the handler the payload dict, not the
    Frame itself (regression for #154): passing the raw Frame raised
    "'Frame' object is not subscriptable" inside the detached task, so the
    session was never removed and its admission slot leaked. Unlike calling
    `_handle_close_session` directly, driving the frame through `_consume`
    exercises the actual dispatch wiring where the bug lived."""
    import agent.control.channel as ch_module

    admission = _admission(profile="single")
    await ch_module._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    assert admission.running_count == 1

    close_frame = Frame(
        type=FrameType.CLOSE_SESSION, payload={"session_id": "s1"}
    ).model_dump_json()

    class _OneShotWS(_FakeWS):
        async def __aiter__(self):
            yield close_frame

    ws = _OneShotWS()
    await ch_module._consume(ws, tmp_path, admission)
    await asyncio.sleep(0.05)  # let the detached close task run

    assert session.get("s1") is None
    assert admission.running_count == 0
    assert FrameType.SESSION_CLOSED in _frame_types(ws)


# ── _consume frame handling (#156) ────────────────────────────────────────────


class _IterWS:
    """A websocket that yields a fixed script of frames, then ends the loop."""

    def __init__(self, msgs):
        self._msgs = msgs
        self.sent: list[str] = []

    async def send(self, msg):
        self.sent.append(msg)

    async def __aiter__(self):
        for msg in self._msgs:
            yield msg


@pytest.mark.parametrize(
    ("frame_type", "handler"),
    [
        (FrameType.OPEN_SESSION, "_traced_open_session"),
        (FrameType.CLOSE_SESSION, "_handle_close_session"),
    ],
)
async def test_consume_holds_strong_ref_to_session_tasks(
    tmp_path, monkeypatch, frame_type, handler
):
    """The session-lifecycle handlers run as detached tasks, and the event loop
    keeps only *weak* references to tasks — so a bare create_task() whose result
    nobody holds may be garbage-collected mid-execution. The query/statement paths
    are safe because _in_flight holds their tasks; these two had no ref at all.

    Asserted structurally (the task is registered while it runs) rather than by
    forcing a collection: a task suspended on a sleep is kept alive by its timer
    handle, so gc.collect() would pass even against the bug.
    """
    import agent.control.channel as ch_module

    release = asyncio.Event()
    registered: list[set] = []

    async def _blocking_handler(ws, msg, admission):
        # Snapshot the registry while this task is mid-flight — the exact window
        # in which a weakly-referenced task could be collected.
        registered.append(set(ch_module._background_tasks))
        await release.wait()

    monkeypatch.setattr(ch_module, handler, _blocking_handler)

    payload = {"session_id": "s1"}
    ws = _IterWS([Frame(type=frame_type, payload=payload).model_dump_json()])
    await ch_module._consume(ws, tmp_path, _admission())
    await asyncio.sleep(0)  # let the spawned task reach its first await

    assert registered and registered[0], "handler task must be strongly referenced while running"
    current = asyncio.current_task()
    assert any(t is not current for t in registered[0])

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)  # let the task finish and its done-callback run
    assert ch_module._background_tasks == set(), "the finished task must not leak"


async def test_consume_survives_unparseable_frame(tmp_path):
    """One malformed frame must not kill the channel: _consume's caller only
    catches (ConnectionClosed, OSError), so a ValidationError escaping here would
    stop the reconnect loop entirely."""
    import agent.control.channel as ch_module

    admission = _admission(profile="decaying_3")
    ws = _IterWS(
        [
            "{not valid json",
            Frame(type=FrameType.SET_CONCURRENCY, payload={"profile": "single"}).model_dump_json(),
        ]
    )
    await ch_module._consume(ws, tmp_path, admission)
    # The loop kept going and handled the frame after the bad one.
    assert admission.active_profile == "single"


async def test_consume_ignores_unknown_frame_type(tmp_path):
    """An unknown/unhandled type is logged and skipped, not fatal."""
    import agent.control.channel as ch_module

    admission = _admission(profile="decaying_3")
    ws = _IterWS(
        [
            Frame(type=FrameType.AUTH_OK, payload={}).model_dump_json(),
            Frame(type=FrameType.SET_CONCURRENCY, payload={"profile": "single"}).model_dump_json(),
        ]
    )
    await ch_module._consume(ws, tmp_path, admission)
    assert admission.active_profile == "single"


# ── CLOSE_SESSION against an in-flight open ───────────────────────────────────
#
# A session only enters `session._sessions` once its open has finished, so every
# test above that opens a session by awaiting `_handle_open_session` to
# completion skips the window these cover. That window is not an edge case: the
# control plane reaps a session stuck in `opening` at its own deadline and sends
# CLOSE_SESSION for it, which under load lands while the open is still queued or
# still on the executor. Leaking there costs the agent budget permanently — it
# only comes back on restart.


class _ScriptedWS(_FakeWS):
    """Yields OPEN, waits for the test to say when, then yields CLOSE."""

    def __init__(self, session_id: str, release: asyncio.Event) -> None:
        super().__init__()
        self._session_id = session_id
        self._release = release

    async def __aiter__(self):
        yield Frame(
            type=FrameType.OPEN_SESSION, payload={"session_id": self._session_id}
        ).model_dump_json()
        await self._release.wait()
        yield Frame(
            type=FrameType.CLOSE_SESSION, payload={"session_id": self._session_id}
        ).model_dump_json()


async def test_close_frees_the_slot_of_an_open_still_queued(tmp_path):
    """The reaper's CLOSE_SESSION must free a session still waiting for capacity.

    `session.remove` returns False for a session that never registered, and the
    old handler discarded that — so the queued `acquire()` waiter stayed in the
    queue holding its claim forever."""
    import agent.control.channel as ch_module

    admission = _admission(profile="single")
    await ch_module._handle_open_session(_FakeWS(), {"session_id": "held"}, admission)
    assert admission.running_count == 1  # the only slot is taken

    release = asyncio.Event()
    ws = _ScriptedWS("queued", release)
    consume = asyncio.create_task(ch_module._consume(ws, tmp_path, admission))
    await asyncio.sleep(0.05)
    assert admission.queued_count == 1, "the second open should be waiting for capacity"

    release.set()
    await consume
    await asyncio.sleep(0.05)  # let the detached close task run

    assert admission.queued_count == 0, "the close must drop the queued waiter"
    assert session.get("queued") is None
    assert admission.running_count == 1  # still just the held session

    await session.remove("held", admission)
    assert admission.running_count == 0


async def test_close_frees_the_slot_of_an_open_still_on_the_executor(tmp_path, monkeypatch):
    """A close landing mid-`open_and_attach` must still free the reservation.

    Cancelling the task cannot stop the worker thread, so the open finishes and
    is responsible for closing the connection it built and handing the slot back
    without registering."""
    import agent.control.channel as ch_module

    in_open = threading.Event()
    finish_open = threading.Event()
    closed: list[object] = []

    class _FakeConn:
        def execute(self, sql):  # SET memory_limit / SET threads
            return self

        def close(self):
            closed.append(self)

    def _blocking_open(**kwargs):
        in_open.set()
        finish_open.wait(timeout=5)
        return _FakeConn()

    monkeypatch.setattr(ch_module, "open_and_attach", _blocking_open)

    admission = _admission(profile="single")
    release = asyncio.Event()
    ws = _ScriptedWS("s1", release)
    consume = asyncio.create_task(ch_module._consume(ws, tmp_path, admission))

    await asyncio.to_thread(in_open.wait, 5)
    assert admission.running_count == 1, "the open holds its reservation while it runs"

    release.set()
    await consume
    await asyncio.sleep(0.05)  # let the detached close task run
    finish_open.set()
    for _ in range(50):  # the open resumes on the executor and cleans up
        await asyncio.sleep(0.02)
        if admission.running_count == 0:
            break

    assert admission.running_count == 0, "the abandoned open must release its reservation"
    assert closed, "the connection it built must be closed, not leaked"
    assert session.get("s1") is None, "an abandoned open must not register"
    assert FrameType.SESSION_OPENED not in _frame_types(ws), (
        "the control plane already failed this session; do not report it open"
    )


async def test_cancelling_an_open_does_not_leak_its_reservation():
    """CancelledError is a BaseException, so the handler's `except Exception`
    never caught it and a cancelled open leaked its slot."""
    import agent.control.channel as ch_module

    in_open = threading.Event()
    finish_open = threading.Event()

    def _blocking_open(**kwargs):
        in_open.set()
        finish_open.wait(timeout=5)
        raise AssertionError("unreachable in this test")

    admission = _admission(profile="single")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ch_module, "open_and_attach", _blocking_open)
        task = asyncio.create_task(
            ch_module._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
        )
        await asyncio.to_thread(in_open.wait, 5)
        assert admission.running_count == 1

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        finish_open.set()

    assert admission.running_count == 0


async def test_a_queued_open_fails_fast_instead_of_hanging(monkeypatch):
    """A queued open must not outwait the control plane's opening deadline.

    Queries queue indefinitely by design; an open cannot, because the API fails
    the row at its own deadline and the client is left hanging until then."""
    import agent.control.channel as ch_module

    monkeypatch.setattr(ch_module.settings, "session_queued_timeout_s", 0.05)
    admission = _admission(profile="single")
    await ch_module._handle_open_session(_FakeWS(), {"session_id": "held"}, admission)

    ws = _FakeWS()
    await ch_module._handle_open_session(ws, {"session_id": "queued"}, admission)

    sent = [Frame.model_validate_json(m) for m in ws.sent]
    assert [f.type for f in sent] == [FrameType.SESSION_OPENED]
    assert sent[0].payload["status"] == "failed"
    assert admission.queued_count == 0
    assert admission.running_count == 1


async def test_repeated_reaped_bursts_do_not_erode_capacity(tmp_path):
    """The whole bug, in one assertion: capacity must survive repeated bursts.

    Before the fix each burst permanently consumed the budget of every session
    the control plane reaped mid-open, so the same agent admitted fewer and fewer
    sessions until a single open failed on a completely idle agent."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    per_session = ch_module._session_reservation_request(admission).memory_bytes
    capacity = admission.budget_bytes // per_session
    assert capacity >= 2, "test needs a budget that fits at least two sessions"

    def _burst(round_no: int) -> list[str]:
        return [f"r{round_no}-s{i}" for i in range(capacity + 2)]  # 2 more than fit

    for round_no in range(3):
        ids = _burst(round_no)
        ws = _IterWS(
            [
                Frame(type=FrameType.OPEN_SESSION, payload={"session_id": sid}).model_dump_json()
                for sid in ids
            ]
        )
        await ch_module._consume(ws, tmp_path, admission)
        await asyncio.sleep(0.2)
        opened = [sid for sid in ids if session.get(sid) is not None]
        assert len(opened) == capacity, (
            f"round {round_no}: admitted {len(opened)} of {capacity} — "
            "capacity eroded across bursts"
        )

        # The control plane reaps everything, opened and still-queued alike.
        close_ws = _IterWS(
            [
                Frame(type=FrameType.CLOSE_SESSION, payload={"session_id": sid}).model_dump_json()
                for sid in ids
            ]
        )
        await ch_module._consume(close_ws, tmp_path, admission)
        await asyncio.sleep(0.2)
        assert admission.running_count == 0, f"round {round_no}: slots leaked after close"
        assert admission.queued_count == 0, f"round {round_no}: waiters leaked after close"


# ── session statements are sized to their own workload ────────────────────────
#
# A held session reserves only an idle baseline; each statement grows to its own
# EXPLAIN estimate and shrinks back. Before this, every session statement ran
# under one flat reservation fixed at open, so the `auto` profile's estimator —
# the whole point of `auto` — never applied to any session traffic at all.


def _session_state(admission, memory_bytes, threads=1):
    """A registered session holding a real reservation, with a stub connection."""
    import agent.control.channel as ch_module

    reservation = admission._try_admit(  # noqa: SLF001 - set up the held grant directly
        ReservationRequest(memory_bytes=memory_bytes, threads=threads)
    )
    assert reservation is not None
    state = session.SessionState(
        session_id="s1",
        conn=object(),
        reservation=reservation,
        memory_bytes=reservation.memory_bytes,
        threads=reservation.threads,
        opened_at=0.0,
        last_active_at=0.0,
    )
    session.register(state)
    return ch_module, state


async def test_statement_grows_the_reservation_to_its_estimate(monkeypatch):
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    baseline = state.memory_bytes

    # A heavy estimate: more than the baseline, less than the whole budget.
    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)
    await ch._resize_for_statement(state, "SELECT 1", admission)

    assert state.memory_bytes > baseline, "a heavy statement must get more than the idle baseline"
    assert state.memory_bytes == state.reservation.total_bytes
    assert admission.committed_fraction <= 1.0

    await ch._shrink_to_baseline(state, admission)
    # The *required* tier is what must come back; the cache grant on top is
    # deliberately kept so the next statement does not re-read what this one read.
    assert state.reservation.memory_bytes == baseline, "the required floor must return"
    assert state.memory_bytes >= baseline


async def test_an_unestimable_statement_gets_the_fallback_bucket(monkeypatch):
    """None covers every DDL/DML statement, not just an EXPLAIN failure, and those
    are not cheap: an Iceberg `CREATE TABLE … AS SELECT` needs a ~76 MiB Parquet
    row-group buffer in one allocation however few rows it writes, so leaving it at
    the idle baseline OOMs it outright (caught by the cross-component suite, not by
    this file, because it only reproduces against a real attached catalog)."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    baseline = state.memory_bytes
    expected = int(
        BUCKET_FRACTIONS[ch_module.settings.estimate_fallback_bucket] * admission.budget_bytes
    )

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: None)
    await ch._resize_for_statement(state, "CREATE TABLE t (i INT)", admission)

    # The fallback bucket is the *required* floor. `state.memory_bytes` is what
    # DuckDB is told it may use, which is that floor plus the revocable cache
    # grant on top, so the guarantee this test exists for lives on the reservation.
    assert state.reservation.memory_bytes == expected
    assert state.memory_bytes >= expected

    await ch._shrink_to_baseline(state, admission)
    assert state.reservation.memory_bytes == baseline


async def test_statement_runs_at_a_partial_size_when_the_budget_is_tight(monkeypatch):
    """Growth is best-effort: a statement that cannot get its full estimate still
    runs, at whatever the agent could spare."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    baseline = state.memory_bytes
    # Something else takes almost everything left.
    free = admission.budget_bytes - int(admission.committed_fraction * admission.budget_bytes)
    hog = admission._try_admit(ReservationRequest(memory_bytes=free - 32 * 1024**2, threads=1))  # noqa: SLF001
    assert hog is not None

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: admission.budget_bytes)
    await ch._resize_for_statement(state, "SELECT 1", admission)

    assert baseline < state.memory_bytes < admission.budget_bytes, "grew, but only partially"
    assert admission.committed_fraction <= 1.0


async def test_estimate_failure_falls_back_rather_than_failing_the_statement(monkeypatch):
    """An estimate that blows up leaves the statement unestimable, which is the
    fallback bucket's whole purpose — the same treatment the one-shot path gives
    it. Leaving the session on its idle baseline instead would run the statement
    in 64 MiB, which is how an Iceberg `CREATE TABLE … AS SELECT` OOMs outright."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    baseline = state.memory_bytes
    fallback = int(
        BUCKET_FRACTIONS[ch_module.settings.estimate_fallback_bucket] * admission.budget_bytes
    )

    def _boom(*a, **k):
        raise RuntimeError("EXPLAIN exploded")

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", _boom)
    await ch._resize_for_statement(state, "SELECT 1", admission)

    assert state.reservation.memory_bytes == fallback > baseline


async def test_a_hanging_explain_is_interrupted_and_falls_back(monkeypatch):
    """DuckDB's planner can spin inside EXPLAIN itself — seen twice on TPC-H Q08
    against SF10. The session estimate path had no timeout at all, so the statement
    never started, a core burned, and nothing unwound it: the statement's own
    timeout only covers execution, which had not begun."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    baseline = state.memory_bytes
    monkeypatch.setattr(ch_module.settings, "explain_timeout_s", 0.05)

    interrupted = threading.Event()

    class _SpinningConn:
        """Blocks in EXPLAIN until somebody interrupts it, like the real thing."""

        def interrupt(self):
            interrupted.set()

    state.conn = _SpinningConn()

    def _hang(conn, sql, **kwargs):
        if not interrupted.wait(timeout=10):
            raise AssertionError("EXPLAIN was never interrupted")
        raise RuntimeError("INTERRUPT: query interrupted")  # what DuckDB raises

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", _hang)
    await asyncio.wait_for(ch._resize_for_statement(state, "SELECT 1", admission, 30.0), timeout=10)

    assert interrupted.is_set(), "the hung EXPLAIN was never interrupted"
    assert state.reservation.memory_bytes > baseline, "did not fall back to a usable size"


async def test_static_profile_sessions_are_left_alone(monkeypatch):
    """Static ladders hand out whole slots, which do not decompose into a
    baseline plus growth. A session under one keeps the slot it was admitted
    with."""
    import agent.control.channel as ch_module

    admission = _admission(profile="single")
    reservation = await admission.acquire()
    state = session.SessionState(
        session_id="s1",
        conn=object(),
        reservation=reservation,
        memory_bytes=reservation.memory_bytes,
        threads=reservation.threads,
        opened_at=0.0,
        last_active_at=0.0,
    )
    session.register(state)
    before = state.memory_bytes

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 1)
    await ch_module._resize_for_statement(state, "SELECT 1", admission)
    await ch_module._shrink_to_baseline(state, admission)

    assert state.memory_bytes == before


async def test_concurrent_sessions_never_oversubscribe_while_growing(monkeypatch):
    """Fairness under the shape this change actually introduces: several sessions
    holding baselines, each trying to grow past what is left."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    states = []
    for i in range(6):
        reservation = admission._try_admit(  # noqa: SLF001
            ReservationRequest(memory_bytes=64 * 1024**2, threads=1)
        )
        assert reservation is not None
        states.append(
            session.SessionState(
                session_id=f"s{i}",
                conn=object(),
                reservation=reservation,
                memory_bytes=reservation.memory_bytes,
                threads=reservation.threads,
                opened_at=0.0,
                last_active_at=0.0,
            )
        )

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: admission.budget_bytes)
    await asyncio.gather(
        *(ch_module._resize_for_statement(st, "SELECT 1", admission) for st in states)
    )
    assert admission.committed_fraction <= 1.0, "concurrent growth oversubscribed the budget"

    for st in states:
        await ch_module._shrink_to_baseline(st, admission)
    # Every *required* tier is back at the baseline. The sessions still hold cache
    # grants on top, which is why this checks the reservations rather than
    # `committed_fraction` — but between them they still fit in the budget.
    assert all(st.reservation.memory_bytes == 64 * 1024**2 for st in states)
    assert admission.committed_fraction <= 1.0


# ── the statement's resource slice: CPU, and revocable cache memory ───────────


class _RecordingConn:
    """A stand-in connection that only records the `SET`s applied to it."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)
        return self


async def test_a_cheap_statement_still_gets_every_core(monkeypatch):
    """The regression that made 21 of 22 TPC-H queries single-threaded: threads
    were scaled by the *memory* bucket, so anything that estimated small ran on
    one core however much scanning it actually did."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)

    # The smallest bucket there is — a scan-heavy query aggregating to one row.
    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 1)
    await ch._resize_for_statement(state, "SELECT sum(x) FROM big", admission)

    assert state.threads == admission.cores


async def test_an_idle_agent_lends_its_spare_budget_as_cache(monkeypatch):
    """DuckDB's `memory_limit` also caps EXTERNAL_FILE_CACHE, so a statement sized
    to its operator working set alone re-reads its Parquet from object storage on
    every pass. Idle budget is lent to it instead."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 1)
    await ch._resize_for_statement(state, "SELECT sum(x) FROM big", admission)

    assert state.reservation.elastic_bytes > 0
    assert state.memory_bytes == state.reservation.total_bytes
    assert state.memory_bytes > state.reservation.memory_bytes, "grew beyond the required floor"
    assert admission.committed_fraction <= 1.0


async def test_the_cache_grant_survives_between_statements(monkeypatch):
    """Handing it back at the end of every statement would evict the cache it
    exists to hold, which is the whole benefit."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    state.conn = _RecordingConn()

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 1)
    await ch._resize_for_statement(state, "SELECT 1", admission)
    lent = state.reservation.elastic_bytes
    await ch._shrink_to_baseline(state, admission)

    assert state.reservation.memory_bytes == 64 * 1024**2, "the required floor went back"
    assert state.reservation.elastic_bytes >= lent, "the cache grant stayed"


async def test_what_a_session_keeps_does_not_depend_on_what_it_just_ran(monkeypatch):
    """The cache grant is sized "ceiling minus required", so carrying it across the
    shrink unchanged made an idle session's cache inversely proportional to the
    weight of its last statement — and zero at the largest bucket, where required
    already exceeds the ceiling. That evicted the whole file cache after every
    heavy statement and cost the five heaviest SF10 queries 2.5-5x."""
    import agent.control.channel as ch_module

    kept = {}
    for label in ("cheap", "heaviest"):
        admission = _admission(profile="auto")
        ch, state = _session_state(admission, 64 * 1024**2)
        state.conn = _RecordingConn()
        # "heaviest" overflows every bucket, so it lands in the top one, whose
        # required reservation is the whole budget and leaves no room under the
        # ceiling for a cache grant.
        estimate = 1 if label == "cheap" else admission.budget_bytes * 10
        monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: estimate)
        await ch._resize_for_statement(state, "SELECT 1", admission)
        await ch._shrink_to_baseline(state, admission)
        kept[label] = state.memory_bytes
        session._sessions.clear()  # noqa: SLF001 - the registry is process-global

    assert kept["heaviest"] > 64 * 1024**2, "the heaviest statement's session kept no cache"
    assert kept["heaviest"] == kept["cheap"], "retention still depends on the last statement"


async def test_one_session_cannot_take_the_whole_budget_as_cache(monkeypatch):
    """Without a fair-share bound the first session to ask takes everything free and
    every session behind it runs on the bare idle baseline — measured on a 22-way
    SF10 burst as one session at 2,342 MiB and twenty-one at 64 MiB."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    states = []
    for i in range(8):
        reservation = admission._try_admit(  # noqa: SLF001
            ReservationRequest(memory_bytes=64 * 1024**2, threads=admission.cores)
        )
        assert reservation is not None
        st = session.SessionState(
            session_id=f"s{i}",
            conn=_RecordingConn(),
            reservation=reservation,
            memory_bytes=reservation.memory_bytes,
            threads=reservation.threads,
            opened_at=0.0,
            last_active_at=0.0,
        )
        session.register(st)
        states.append(st)

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 1)
    for st in states:
        await ch_module._resize_for_statement(st, "SELECT 1", admission)

    grants = [st.reservation.elastic_bytes for st in states]
    assert admission.committed_fraction <= 1.0
    assert all(g <= admission.budget_bytes // len(states) for g in grants), (
        f"a session took more than its fair share: {grants}"
    )
    assert sum(1 for st in states if st.memory_bytes > 64 * 1024**2) >= len(states) - 1, (
        "sessions were starved at the idle baseline while one held the budget"
    )


async def test_a_starved_statement_waits_for_budget_instead_of_running_tiny(monkeypatch):
    """22 simultaneous SF10 queries at the 64 MiB idle baseline spilled hard enough
    to take the agent process down. A statement that cannot reach a workable share
    of its estimate now waits for room rather than running into that."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    state.conn = _RecordingConn()
    # Somebody else holds effectively the whole budget, and is executing, so the
    # deadlock guard does not fire.
    hog = admission._try_admit(  # noqa: SLF001
        ReservationRequest(memory_bytes=admission.budget_bytes - 64 * 1024**2, threads=1)
    )
    assert hog is not None
    busy = session.SessionState(
        session_id="busy",
        conn=_RecordingConn(),
        reservation=hog,
        memory_bytes=hog.memory_bytes,
        threads=hog.threads,
        opened_at=0.0,
        last_active_at=0.0,
    )
    session.register(busy)

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)

    async def _statement() -> None:
        # Sizing runs inside the session lock, exactly as _handle_exec_statement
        # does it — which is also what makes this session count as "executing".
        async with state.lock:
            await ch._resize_for_statement(state, "SELECT 1", admission, 30.0)

    async with busy.lock:  # `busy` is mid-statement, so waiting can pay off
        sizing = asyncio.create_task(_statement())
        await asyncio.sleep(0.05)
        assert not sizing.done(), "ran at the idle baseline instead of waiting"
        assert admission.growth_waiting == 1

    admission.release(hog)  # the other statement finishes
    await asyncio.wait_for(sizing, timeout=5)

    assert state.reservation.memory_bytes > 64 * 1024**2, "did not grow once budget freed"
    assert state.admission_wait_ms > 0


def _idle_session(admission, session_id: str, memory_bytes: int):
    """A registered session holding a reservation but running nothing.

    Nobody is going to release what it holds, so it is the honest model of
    "budget that will never come free" — unlike a bare reservation, which the
    guard rightly reads as a one-shot query that *will* release.
    """
    reservation = admission._try_admit(  # noqa: SLF001
        ReservationRequest(memory_bytes=memory_bytes, threads=admission.cores)
    )
    assert reservation is not None
    state = session.SessionState(
        session_id=session_id,
        conn=_RecordingConn(),
        reservation=reservation,
        memory_bytes=reservation.memory_bytes,
        threads=reservation.threads,
        opened_at=0.0,
        last_active_at=0.0,
    )
    session.register(state)
    return state


async def test_a_statement_does_not_wait_when_nothing_can_free_budget(monkeypatch):
    """The tie-break growth has to have: every waiter is holding memory while asking
    for more, so waiting when nobody is executing would just burn the deadline."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    state.conn = _RecordingConn()
    _idle_session(admission, "idle-hog", admission.budget_bytes - 128 * 1024**2)

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)
    async with state.lock:  # only this session is executing, and it is us
        await asyncio.wait_for(
            ch._resize_for_statement(state, "SELECT 1", admission, 30.0), timeout=5
        )

    assert state.admission_wait_ms < 1000, "waited even though nothing could free budget"


async def test_waiters_are_not_mistaken_for_executors(monkeypatch):
    """The guard that could not fire. A waiting statement holds its session lock for
    the whole wait, so `executing_count()` alone cannot tell a parked session from a
    working one — during the live deadlock it read 10 "executors", none of which
    would ever release anything, and every waiter sat out the full 300s timeout."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    baseline = 64 * 1024**2
    ch, state = _session_state(admission, baseline)
    state.conn = _RecordingConn()
    # Three sessions parked in the wait loop (locks held, nothing executing) plus a
    # hog, sized so almost nothing is free and our statement would park too.
    parked = [_idle_session(admission, f"parked{i}", baseline) for i in range(3)]
    _idle_session(admission, "idle-hog", admission.budget_bytes - 5 * baseline)
    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)

    async def _park(st):
        async with st.lock:
            await admission.await_growth(admission.budget_bytes, 5.0)

    tasks = [asyncio.create_task(_park(st)) for st in parked]
    await asyncio.sleep(0.05)
    # The trap: all three are asleep, yet every one of them holds its lock, so the
    # naive "is anybody executing?" count sees three busy sessions (four once we
    # take our own lock below) and none of them will ever release anything.
    assert admission.growth_waiting == 3
    assert session.executing_count() == 3, "parked sessions do look like executors"

    async with state.lock:
        await asyncio.wait_for(
            ch._resize_for_statement(state, "SELECT 1", admission, 30.0), timeout=5
        )

    assert state.admission_wait_ms < 1000, "waited on sessions that were themselves parked"
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_a_parked_statement_holds_only_its_baseline(monkeypatch):
    """The deadlock in one assertion. Waiters used to keep whatever partial grant
    each retry won, so they collectively absorbed the budget they were all waiting
    for — ten of them once held 100.000000% of a 4 GiB agent's budget, to the byte,
    and only the 300s timeout broke it."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    baseline = 64 * 1024**2
    ch, state = _session_state(admission, baseline)
    state.conn = _RecordingConn()
    # Someone else is executing, so waiting is worthwhile and the guard stays quiet.
    # It holds nearly everything, so our statement cannot reach its floor.
    busy = _idle_session(admission, "busy", admission.budget_bytes - 2 * baseline)

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)

    async def _statement():
        async with state.lock:
            await ch._resize_for_statement(state, "SELECT 1", admission, 30.0)

    async with busy.lock:
        sizing = asyncio.create_task(_statement())
        await asyncio.sleep(0.05)
        assert admission.growth_waiting == 1, "did not park"
        assert state.reservation.total_bytes <= baseline, (
            f"parked while holding {state.reservation.total_bytes} bytes, "
            f"not the {baseline}-byte baseline"
        )

    admission.release(busy.reservation)
    await asyncio.wait_for(sizing, timeout=5)
    assert state.reservation.memory_bytes > baseline, "did not grow once budget freed"


async def test_a_running_one_shot_query_is_worth_waiting_for(monkeypatch):
    """A one-shot query holds budget and will release it, but is not in the session
    registry — so `executing_count()` cannot see it. Without counting it, a waiter
    gives up the moment the only other consumer is a one-shot query."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    baseline = 64 * 1024**2
    ch, state = _session_state(admission, baseline)
    state.conn = _RecordingConn()
    # Not a session: exactly the shape `_handle_dispatch` acquires.
    one_shot = admission._try_admit(  # noqa: SLF001
        ReservationRequest(memory_bytes=admission.budget_bytes - 2 * baseline, threads=1)
    )
    assert one_shot is not None

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)

    async def _statement():
        async with state.lock:
            await ch._resize_for_statement(state, "SELECT 1", admission, 30.0)

    sizing = asyncio.create_task(_statement())
    await asyncio.sleep(0.05)
    assert admission.growth_waiting == 1, "gave up while a one-shot query was still running"

    admission.release(one_shot)
    await asyncio.wait_for(sizing, timeout=5)
    assert state.reservation.memory_bytes > baseline


async def test_wait_deadlines_are_jittered(monkeypatch):
    """Ten timeouts firing in an 8-second window released ten statements at once and
    took the agent down 33 seconds later. Spreading the deadlines removes the herd."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    baseline = 64 * 1024**2
    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)
    monkeypatch.setattr(ch_module.settings, "statement_admission_wait_s", 0.4)
    # Nothing else is executing, so each statement bails out of the wait immediately
    # after computing its deadline; what we are pinning is that the deadlines differ.
    seen: list[float] = []
    real_uniform = ch_module.random.uniform
    monkeypatch.setattr(
        ch_module.random, "uniform", lambda a, b: seen.append(real_uniform(a, b)) or seen[-1]
    )

    for _ in range(8):
        _, st = _session_state(admission, baseline)
        st.conn = _RecordingConn()
        async with st.lock:
            await ch_module._resize_for_statement(st, "SELECT 1", admission, 30.0)
        # Hand the whole reservation back before the next one, or the budget runs
        # out after a couple of iterations and `_try_admit` starts refusing.
        admission.release(st.reservation)
        session._sessions.clear()  # noqa: SLF001

    assert len(seen) == 8
    assert len(set(seen)) > 1, "every waiter got an identical deadline"
    assert all(0.85 <= f <= 1.15 for f in seen), f"jitter escaped its bounds: {seen}"


async def test_the_watchdog_rescues_a_queue_that_went_quiet_after_parking(monkeypatch):
    """The V2 failure exactly. A statement checks before parking whether anything is
    left to free budget, but the agent can go quiet *after* it parks — and a parked
    statement re-evaluates nothing. Ten of them sat for 255 seconds on an idle agent
    and then all resumed at once. The metrics-loop watchdog releases them one at a
    time instead."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    baseline = 64 * 1024**2
    ch, state = _session_state(admission, baseline)
    state.conn = _RecordingConn()
    # An executor holds the budget, so parking is the right call when we check...
    busy = _idle_session(admission, "busy", admission.budget_bytes - 2 * baseline)
    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)

    async def _statement():
        async with state.lock:
            await ch._resize_for_statement(state, "SELECT 1", admission, 30.0)

    async with busy.lock:
        sizing = asyncio.create_task(_statement())
        await asyncio.sleep(0.05)
        assert admission.growth_waiting == 1, "did not park"
    # ...and now the executor stops without releasing. Nothing will ever free
    # budget, and the parked statement cannot notice on its own.
    assert ch_module._nobody_can_free_budget(admission, self_is_waiter=False)
    assert not sizing.done(), "should still be parked with no way to notice"

    assert admission.release_growth_head() is True  # what _push_metrics does
    await asyncio.wait_for(sizing, timeout=5)
    assert state.admission_wait_ms < 5000, "waited out the full deadline"


async def test_use_and_set_are_not_charged_a_third_of_the_agent():
    """`USE`/`SET` move no data. Charging them the unestimable fallback bucket had
    every session in a burst claim a third of the budget for a 1 ms statement."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    baseline = 64 * 1024**2
    fallback = int(
        BUCKET_FRACTIONS[ch_module.settings.estimate_fallback_bucket] * admission.budget_bytes
    )

    for sql in ('USE "cat"."schema"', "SET timezone = 'UTC'"):
        assert ch_module._statement_reservation_request(None, admission, baseline, sql) is None, sql
    # Real DDL is still not cheap — an Iceberg CTAS needs a row-group buffer.
    ddl = ch_module._statement_reservation_request(
        None, admission, baseline, "CREATE TABLE t(i INT)"
    )
    assert ddl is not None and ddl.memory_bytes == fallback


async def test_shrinking_resizes_the_connection_not_just_the_accounting(monkeypatch):
    """Accounting-only shrink leaves DuckDB holding the previous statement's limit
    while admission believes the memory is free — the drift that lets two sessions
    between them exceed the cgroup."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    conn = _RecordingConn()
    state.conn = conn

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)
    await ch._resize_for_statement(state, "SELECT 1", admission)
    conn.executed.clear()
    await ch._shrink_to_baseline(state, admission)

    limits = [sql for sql in conn.executed if sql.startswith("SET memory_limit=")]
    assert limits, "the connection was never resized"
    # GiB, not GB: DuckDB reads a `GB` suffix as 10**9 and would hand back ~7% less.
    assert limits[-1].endswith("GiB'")
    assert limits[-1] == f"SET memory_limit='{state.reservation.total_bytes / 1024**3}GiB'"


async def test_a_busy_session_never_has_its_memory_pulled_out_from_under_it(monkeypatch):
    """`is_idle` is the guarantee; without it a reclaim could shrink a connection
    that is mid-scan."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    state.conn = _RecordingConn()

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 1)
    await ch._resize_for_statement(state, "SELECT 1", admission)
    assert state.reservation.elastic_bytes > 0

    async with state.lock:  # a statement is now in flight on this session
        assert not state.is_idle()
        # Something big enough that it can only be satisfied by reclaiming.
        assert (
            admission._try_admit(  # noqa: SLF001
                ReservationRequest(memory_bytes=admission.budget_bytes, threads=1)
            )
            is None
        )
        assert state.reservation.elastic_bytes > 0, "cache was pulled from a running statement"

    assert state.is_idle()


async def test_the_estimate_is_reused_across_identical_statements(monkeypatch):
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    calls = []

    def _estimate(conn, sql, **kwargs):
        calls.append(sql)
        return 1

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", _estimate)
    for _ in range(3):
        await ch._resize_for_statement(state, "SELECT 1", admission)

    assert calls == ["SELECT 1"], "re-EXPLAINed a statement it had already planned"


async def test_a_non_select_invalidates_remembered_estimates(monkeypatch):
    """DDL can change what a later plan binds to, so everything remembered on this
    connection is suspect once one runs."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    ch, state = _session_state(admission, 64 * 1024**2)
    calls = []

    def _estimate(conn, sql, **kwargs):
        calls.append(sql)
        return 1

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", _estimate)
    await ch._resize_for_statement(state, "SELECT 1", admission)
    await ch._resize_for_statement(state, "CREATE TABLE t (i INT)", admission)
    await ch._resize_for_statement(state, "SELECT 1", admission)

    assert calls.count("SELECT 1") == 2, "kept an estimate across a DDL statement"


async def test_exec_statement_sizes_the_session_to_the_statement(tmp_path, monkeypatch):
    """End to end through the real handler, on a real connection: the wiring from
    EXEC_STATEMENT to the estimator is the thing that was missing, so testing
    `_resize_for_statement` alone would not catch it being unhooked again."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    await ch_module._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    baseline = state.memory_bytes

    seen: list[int] = []
    real_run = ch_module.run_statement

    async def _spy(sql, path, timeout_s, **kwargs):
        seen.append(kwargs["memory_bytes"])
        return await real_run(sql, path, timeout_s, **kwargs)

    monkeypatch.setattr(ch_module, "run_statement", _spy)
    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)

    await ch_module._handle_exec_statement(
        _FakeWS(),
        {"session_id": "s1", "query_id": "q1", "sql": "SELECT 1 AS n"},
        tmp_path,
        admission,
    )

    assert seen and seen[0] > baseline, "the statement must run at its estimated size"
    assert state.reservation.memory_bytes == baseline, "the required floor must return after"
    assert admission.committed_fraction <= 1.0
    await session.remove("s1", admission)


async def test_exec_statement_returns_to_baseline_when_the_statement_fails(tmp_path, monkeypatch):
    """The shrink is in a finally for a reason: a failing heavy query must not
    leave the session pinning a large reservation for the rest of its life."""
    import agent.control.channel as ch_module

    admission = _admission(profile="auto")
    await ch_module._handle_open_session(_FakeWS(), {"session_id": "s1"}, admission)
    state = session.get("s1")
    baseline = state.memory_bytes

    monkeypatch.setattr(ch_module, "estimate_memory_bytes", lambda *a, **k: 700 * 1024**2)
    ws = _FakeWS()
    await ch_module._handle_exec_statement(
        ws, {"session_id": "s1", "query_id": "q1", "sql": "SELECT * FROM nope"}, tmp_path, admission
    )

    assert Frame.model_validate_json(ws.sent[-1]).payload["status"] == "failed"
    assert state.reservation.memory_bytes == baseline
    await session.remove("s1", admission)
