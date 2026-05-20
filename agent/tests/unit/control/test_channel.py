import asyncio
import uuid

import websockets

from duckhaven_shared.protocol import Frame, FrameType


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


async def test_dispatch_sends_done_frame(tmp_path, monkeypatch):
    """Dispatch query frame triggers runner and agent sends QUERY_DONE back."""
    import agent.control.channel as ch_module
    import agent.executor.supervisor as sup_module

    query_id = str(uuid.uuid4())
    done_frames: list[Frame] = []

    async def mock_run_query(sql, result_path, memory_limit_gb, timeout_s, **kwargs):
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
                "memory_limit_gb": 1.0,
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


async def test_dispatch_error_sends_failed_frame(tmp_path, monkeypatch):
    """When run_query raises, the channel sends QUERY_DONE with status=failed."""
    import agent.control.channel as ch_module

    query_id = str(uuid.uuid4())
    done_frames: list[Frame] = []

    async def failing_run_query(sql, result_path, memory_limit_gb, timeout_s, **kwargs):
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
                    "memory_limit_gb": 1.0,
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

    async def slow_run_query(sql, result_path, memory_limit_gb, timeout_s, **kwargs):
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
                    "memory_limit_gb": 1.0,
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
