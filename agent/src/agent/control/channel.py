import asyncio
import logging
import platform
import uuid
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed

from agent.auth import TokenHolder
from agent.config import settings
from agent.executor.supervisor import run_query
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.schemas import AgentCapabilities

logger = logging.getLogger(__name__)

_in_flight: dict[str, asyncio.Task] = {}


def _get_capabilities() -> AgentCapabilities:
    import duckdb

    conn = duckdb.connect()
    version = duckdb.version()
    extensions = [
        row[0]
        for row in conn.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded"
        ).fetchall()
    ]
    conn.close()
    return AgentCapabilities(
        duckdb_version=version,
        extensions=extensions,
        memory_limit_gb=settings.memory_limit_bytes / 1024**3,
        cores=1,
        host=platform.node() or None,
    )


async def _handle_dispatch(ws, payload: dict, results_dir: Path) -> None:
    query_id = payload["query_id"]
    sql = payload["sql"]
    memory_limit_gb = min(float(payload.get("memory_limit_gb", 6.0)), settings.max_memory_limit_gb)
    timeout_s = min(float(payload.get("timeout_s", 600.0)), settings.max_timeout_s)
    backend = payload.get("backend")
    storage_credentials = payload.get("storage_credentials")
    workspace = payload.get("workspace") or {}
    workspace_slug = workspace.get("slug") if isinstance(workspace, dict) else None
    uc_endpoint = (payload.get("unity_catalog") or {}).get("endpoint")
    stats_for = payload.get("stats_for")
    result_path = results_dir / f"{query_id}.parquet"

    progress = Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": query_id})
    await ws.send(progress.model_dump_json())

    try:
        stats = await run_query(
            sql,
            result_path,
            memory_limit_gb,
            timeout_s,
            backend=backend,
            storage_credentials=storage_credentials,
            workspace_slug=workspace_slug,
            uc_endpoint=uc_endpoint,
            stats_for=stats_for,
        )
        done_payload: dict[str, object] = {
            "query_id": query_id,
            "status": "done",
            "row_count": stats["row_count"],
            "duration_ms": stats["duration_ms"],
            "result_path": str(result_path),
        }
        if stats_for:
            done_payload["stats_table"] = {
                "schema": stats_for.get("schema"),
                "table": stats_for.get("table"),
            }
            done_payload["table_row_count"] = stats.get("table_row_count")
            done_payload["table_size_bytes"] = stats.get("table_size_bytes")
        done = Frame(type=FrameType.QUERY_DONE, payload=done_payload)
    except TimeoutError:
        done = Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": query_id, "status": "failed", "error": "timeout"},
        )
    except Exception as exc:
        done = Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": query_id, "status": "failed", "error": str(exc)},
        )
    finally:
        _in_flight.pop(query_id, None)

    await ws.send(done.model_dump_json())


async def run_control_channel(
    results_dir: Path | None = None,
    token_holder: TokenHolder | None = None,
) -> None:
    if results_dir is None:
        results_dir = Path(settings.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            async with websockets.connect(settings.control_plane_url) as ws:
                auth = Frame(
                    type=FrameType.AUTH,
                    payload={"token": settings.bootstrap_token, "name": platform.node()},
                )
                await ws.send(auth.model_dump_json())

                raw = await ws.recv()
                frame = Frame.model_validate_json(raw)
                if frame.type != FrameType.AUTH_OK:
                    logger.error("Auth failed: %s", raw)
                    return

                logger.info("Authenticated as agent %s", frame.payload["agent_id"])
                if token_holder is not None:
                    token_holder.value = frame.payload.get("session_token", "")

                caps = Frame(type=FrameType.AGENT_STATUS, payload=_get_capabilities().model_dump())
                await ws.send(caps.model_dump_json())

                async for raw_msg in ws:
                    msg = Frame.model_validate_json(raw_msg)

                    if msg.type == FrameType.HEARTBEAT:
                        await ws.send(Frame(type=FrameType.HEARTBEAT).model_dump_json())
                        # Re-advertise capabilities so the control plane's stored
                        # doc + last_ping_at stay fresh (G-D17-a).
                        caps = Frame(
                            type=FrameType.AGENT_STATUS, payload=_get_capabilities().model_dump()
                        )
                        await ws.send(caps.model_dump_json())

                    elif msg.type == FrameType.DISPATCH_QUERY:
                        query_id = msg.payload.get("query_id", str(uuid.uuid4()))
                        task = asyncio.create_task(_handle_dispatch(ws, msg.payload, results_dir))
                        _in_flight[query_id] = task

                    elif msg.type == FrameType.CANCEL_QUERY:
                        query_id = msg.payload.get("query_id", "")
                        task = _in_flight.pop(query_id, None)
                        if task:
                            task.cancel()

        except (ConnectionClosed, OSError) as exc:
            logger.warning("Disconnected (%s), reconnecting in 5s", exc)
        await asyncio.sleep(5)
