import asyncio
import uuid

import pytest
import websockets
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

from agent.control import session
from agent.executor.admission import Admission
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.schemas import AgentCapabilities
from duckhaven_shared.telemetry import inject_trace_context


@pytest.fixture(autouse=True)
def _clear_sessions():
    # The session registry is process-global; keep tests isolated (mirrors the
    # fixture in tests/unit/control/test_session.py).
    session._sessions.clear()
    yield
    session._sessions.clear()


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
