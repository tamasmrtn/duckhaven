import asyncio
import logging
import platform
import uuid
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed

from agent.auth import TokenHolder, load_session_token, save_session_token
from agent.config import settings
from agent.executor.supervisor import run_query
from agent.metrics.system import MetricsSampler, cpu_capability, effective_memory_bytes
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.schemas import AgentCapabilities

logger = logging.getLogger(__name__)

_in_flight: dict[str, asyncio.Task] = {}


def _get_capabilities() -> AgentCapabilities:
    import duckdb

    conn = duckdb.connect()
    version = duckdb.version()
    # Load the pre-installed query extensions so they are advertised as available.
    # A fresh connection lists only built-ins under `WHERE loaded`; the storage
    # backends require these (httpfs for S3/MinIO, azure for ADLS, iceberg for
    # the catalog), and dispatch is gated on them being advertised.
    for ext in ("httpfs", "azure", "iceberg"):
        try:
            conn.execute(f"LOAD {ext}")
        except duckdb.Error:
            logger.warning("Extension %s unavailable; not advertising it", ext)
    extensions = [
        row[0]
        for row in conn.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded"
        ).fetchall()
    ]
    conn.close()
    cpu = cpu_capability()
    return AgentCapabilities(
        duckdb_version=version,
        extensions=extensions,
        memory_limit_gb=round(effective_memory_bytes() / 1024**3, 1),
        cores=cpu["cores"],
        cpu_model=cpu["cpu_model"],
        cpu_cores_physical=cpu["cpu_cores_physical"],
        host=platform.node() or None,
    )


async def _handle_dispatch(ws, payload: dict, results_dir: Path) -> None:
    query_id = payload["query_id"]
    sql = payload["sql"]
    timeout_s = min(float(payload.get("timeout_s", 600.0)), settings.max_timeout_s)
    backend = payload.get("backend")
    workspace = payload.get("workspace") or {}
    workspace_slug = workspace.get("slug") if isinstance(workspace, dict) else None
    default_schema = workspace.get("default_schema") if isinstance(workspace, dict) else None
    # Polaris connection info comes from agent config, not the wire; DuckDB
    # does the OAuth2 exchange itself and Polaris vends storage creds on attach.
    polaris = {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }
    stats_for = payload.get("stats_for")
    result_path = results_dir / f"{query_id}.parquet"

    progress = Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": query_id})
    await ws.send(progress.model_dump_json())

    try:
        stats = await run_query(
            sql,
            result_path,
            timeout_s,
            backend=backend,
            workspace_slug=workspace_slug,
            polaris=polaris,
            default_schema=default_schema,
            stats_for=stats_for,
        )
        done_payload: dict[str, object] = {
            "query_id": query_id,
            "status": "done",
            "row_count": stats["row_count"],
            "duration_ms": stats["duration_ms"],
            # Only a SELECT writes a Parquet result file; DDL/DML have none.
            "result_path": str(result_path) if stats.get("wrote_result") else None,
            "result_bytes": stats.get("result_bytes"),
        }
        if stats_for:
            done_payload["stats_table"] = {
                "schema": stats_for.get("schema"),
                "table": stats_for.get("table"),
            }
            done_payload["table_row_count"] = stats.get("table_row_count")
            done_payload["table_size_bytes"] = stats.get("table_size_bytes")
            done_payload["iceberg"] = stats.get("iceberg")
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


async def _push_metrics(ws, sampler: MetricsSampler) -> None:
    """Push live CPU/memory utilization samples on a fixed cadence until cancelled."""
    while True:
        await asyncio.sleep(settings.metrics_sample_interval_s)
        frame = Frame(
            type=FrameType.METRICS_SAMPLE,
            payload=sampler.sample().model_dump(mode="json"),
        )
        await ws.send(frame.model_dump_json())


async def run_control_channel(
    results_dir: Path | None = None,
    token_holder: TokenHolder | None = None,
    session_token_path: Path | None = None,
) -> None:
    if results_dir is None:
        results_dir = Path(settings.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    if session_token_path is None:
        session_token_path = (
            Path(settings.session_token_path)
            if settings.session_token_path
            else results_dir / ".session-token"
        )

    while True:
        try:
            async with websockets.connect(settings.control_plane_url) as ws:
                # Authenticate with the long-lived session token once we have one
                # (held in memory, or persisted from a previous run); fall back to
                # the single-use bootstrap token only for the first registration.
                auth_token = (
                    (token_holder.value if token_holder else "")
                    or load_session_token(session_token_path)
                    or settings.bootstrap_token
                )
                auth = Frame(
                    type=FrameType.AUTH,
                    payload={
                        "token": auth_token,
                        "name": settings.agent_name or platform.node(),
                        # Where the control plane fetches result Parquet. The host
                        # is the socket peer address, observed by the API on accept.
                        "result_port": settings.results_http_port,
                    },
                )
                await ws.send(auth.model_dump_json())

                raw = await ws.recv()
                frame = Frame.model_validate_json(raw)
                if frame.type != FrameType.AUTH_OK:
                    logger.error("Auth failed: %s", raw)
                    return

                logger.info("Authenticated as agent %s", frame.payload["agent_id"])
                session_token = frame.payload.get("session_token", "")
                if token_holder is not None:
                    token_holder.value = session_token
                # Persist so a restart re-authenticates with the session token
                # instead of the now-consumed bootstrap token.
                save_session_token(session_token_path, session_token)

                caps = Frame(type=FrameType.AGENT_STATUS, payload=_get_capabilities().model_dump())
                await ws.send(caps.model_dump_json())

                # Push live utilization on its own cadence; cancelled on disconnect.
                metrics_task = asyncio.create_task(_push_metrics(ws, MetricsSampler()))
                try:
                    await _consume(ws, results_dir)
                finally:
                    metrics_task.cancel()

        except (ConnectionClosed, OSError) as exc:
            logger.warning("Disconnected (%s), reconnecting in 5s", exc)
        await asyncio.sleep(5)


async def _consume(ws, results_dir: Path) -> None:
    async for raw_msg in ws:
        msg = Frame.model_validate_json(raw_msg)

        if msg.type == FrameType.HEARTBEAT:
            await ws.send(Frame(type=FrameType.HEARTBEAT).model_dump_json())
            # Re-advertise capabilities so the control plane's stored
            # doc + last_ping_at stay fresh (G-D17-a).
            caps = Frame(type=FrameType.AGENT_STATUS, payload=_get_capabilities().model_dump())
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
