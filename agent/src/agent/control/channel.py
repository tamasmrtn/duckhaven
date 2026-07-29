import asyncio
import logging
import platform
import time
import uuid
from pathlib import Path

import websockets
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError
from websockets.exceptions import ConnectionClosed

from agent.auth import TokenHolder, load_session_token, save_session_token
from agent.config import settings
from agent.control import session
from agent.executor.admission import (
    Admission,
    QueuedTimeout,
    QueueFull,
    Reservation,
    ReservationRequest,
)
from agent.executor.estimator import bucket_for, estimate_memory_bytes
from agent.executor.runner import open_and_attach
from agent.executor.supervisor import run_query, run_statement
from agent.metrics.system import MetricsSampler, cpu_capability, effective_memory_bytes
from duckhaven_shared.concurrency import BUCKET_FRACTIONS
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.schemas import AgentCapabilities
from duckhaven_shared.telemetry import extract_trace_context, inject_trace_context

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("duckhaven.agent")

_in_flight: dict[str, asyncio.Task] = {}

# Strong refs for handler tasks that are not keyed by a query id (session
# lifecycle). The event loop only keeps weak references to tasks, so a bare
# create_task() can be garbage-collected mid-execution; _in_flight covers the
# query/statement tasks, this covers the rest.
_background_tasks: set[asyncio.Task] = set()

# Control-plane protocol features this agent implements, advertised so the API can
# gate on them without a version number (see duckhaven_shared.schemas).
_PROTOCOL_FEATURES = ["statement_ack"]


def _spawn(coro) -> None:
    """Run a handler as a detached task, holding a strong ref until it finishes."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _frame_ref(payload: dict) -> str:
    """The ids that identify a frame in the logs, for correlating with the API."""
    parts = [f" {key}={payload[key]}" for key in ("session_id", "query_id") if payload.get(key)]
    return "".join(parts)


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
        protocol_features=list(_PROTOCOL_FEATURES),
    )


async def _send_statement_ack(ws, statement_id: str) -> None:
    """Acknowledge receipt of an EXEC_STATEMENT. Receipt only — not success."""
    ack = Frame(type=FrameType.STATEMENT_ACK, payload={"query_id": statement_id})
    await ws.send(ack.model_dump_json())


async def _send_failed(ws, query_id: str, error: str) -> None:
    done = Frame(
        type=FrameType.QUERY_DONE,
        payload={"query_id": query_id, "status": "failed", "error": error},
    )
    await ws.send(done.model_dump_json())


async def _prepare_and_estimate(sql: str, **attach_kwargs) -> tuple[object | None, int | None]:
    """Open+attach a connection and estimate peak memory (best-effort, `auto`).

    Runs on a thread executor with a short EXPLAIN timeout enforced via
    `conn.interrupt()`. Returns `(conn|None, estimate|None)`; the estimator
    swallows an interrupted EXPLAIN as `None`. Never raises into dispatch.
    """
    loop = asyncio.get_running_loop()
    conn_box: dict[str, object] = {}
    # Captured here (event-loop thread, inside handle_dispatch's span) and
    # passed in: run_in_executor does not propagate contextvars to the worker
    # thread, so trace.get_current_span() would see nothing if called from
    # inside _work.
    trace_headers = inject_trace_context()

    def _work() -> int | None:
        conn = open_and_attach(**attach_kwargs, trace_headers=trace_headers)
        conn_box["conn"] = conn
        return estimate_memory_bytes(conn, sql, safety=settings.estimate_safety_multiplier)

    def _interrupt() -> None:
        conn = conn_box.get("conn")
        if conn is not None:
            try:
                conn.interrupt()
            except Exception:  # noqa: BLE001 - interrupt is best-effort
                pass

    handle = loop.call_later(settings.explain_timeout_s, _interrupt)
    try:
        estimate = await loop.run_in_executor(None, _work)
    except Exception as exc:  # noqa: BLE001 - estimation must never drop a query
        logger.warning("Estimate prepare failed: %s", exc)
        estimate = None
    finally:
        handle.cancel()
    return conn_box.get("conn"), estimate


def _build_request(estimate: int | None, admission: Admission) -> ReservationRequest:
    """Map an estimate (or the fallback bucket) to a reservation request."""
    if estimate is None:
        frac = BUCKET_FRACTIONS[settings.estimate_fallback_bucket]
        mem = int(frac * admission.budget_bytes)
    else:
        mem, frac, _ = bucket_for(estimate, admission.budget_bytes, BUCKET_FRACTIONS)
    threads = max(1, round(admission.cores * frac))
    return ReservationRequest(memory_bytes=mem, threads=threads)


async def _traced_dispatch(ws, msg: Frame, results_dir: Path, admission: Admission) -> None:
    """Run _handle_dispatch inside a consumer span continuing the api's trace.

    The span lives here (not in _consume) because dispatch runs as a detached
    task — the extracted context would not propagate into create_task otherwise.
    With no SDK configured the span is a no-op and dispatch runs unchanged.
    """
    with _tracer.start_as_current_span(
        "handle_dispatch",
        context=extract_trace_context(msg.trace_context),
        kind=trace.SpanKind.CONSUMER,
        attributes={"duckhaven.query_id": msg.payload.get("query_id", "")},
    ):
        await _handle_dispatch(ws, msg.payload, results_dir, admission)


def _session_reservation_request(admission: Admission) -> ReservationRequest:
    """Size a held session's reservation: the configured session memory clamped to
    the agent's budget, with threads proportional to that fraction."""
    budget = admission.budget_bytes
    mem = max(1, min(settings.session_reservation_bytes, budget))
    frac = mem / budget
    threads = max(1, round(admission.cores * frac))
    return ReservationRequest(memory_bytes=mem, threads=threads)


async def _send_session_opened(ws, session_id: str, status: str, error: str | None = None) -> None:
    payload: dict[str, object] = {"session_id": session_id, "status": status}
    if error is not None:
        payload["error"] = error
    await ws.send(Frame(type=FrameType.SESSION_OPENED, payload=payload).model_dump_json())


async def _handle_open_session(ws, payload: dict, admission: Admission) -> None:
    """Open a held DuckDB connection for a session and hold an admission slot.

    Acquires a reservation (auto profile sizes it from ``session_reservation_bytes``;
    static profiles take a ladder slot), opens+attaches the connection with the
    API-supplied Polaris credentials, fixes its ``memory_limit``/``threads``, and
    registers it. Any failure releases the slot and reports ``status="failed"`` so
    the control plane marks the session failed rather than pinning the agent."""
    session_id = payload["session_id"]
    catalogs = payload.get("catalogs") or []
    active_catalog = payload.get("active_catalog")
    # Polaris connection info is vended by the API in the frame (the session
    # credential seam); fall back to agent config for older control planes.
    polaris = payload.get("polaris") or {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }

    request = _session_reservation_request(admission)
    try:
        reservation = await admission.acquire(request if admission.is_auto else None)
    except (QueueFull, QueuedTimeout) as exc:
        await _send_session_opened(ws, session_id, "failed", str(exc))
        return
    except asyncio.CancelledError:
        raise

    loop = asyncio.get_running_loop()
    trace_headers = inject_trace_context()

    def _open() -> object:
        conn = open_and_attach(
            catalogs=catalogs,
            active_catalog=active_catalog,
            polaris=polaris,
            trace_headers=trace_headers,
            disabled_filesystems=settings.sandbox_disabled_filesystems,
            lock_config=settings.sandbox_lock_configuration,
        )
        # Fix the session's resource slice once; statements run within it.
        # GiB, not GB — see the note in executor.runner._run_one_statement.
        conn.execute(f"SET memory_limit='{reservation.memory_bytes / 1024**3}GiB'")
        conn.execute(f"SET threads={reservation.threads}")
        return conn

    try:
        conn = await loop.run_in_executor(None, _open)
    except Exception as exc:  # noqa: BLE001 - report and release on any open failure
        admission.release(reservation)
        trace.get_current_span().set_status(Status(StatusCode.ERROR, "session open failed"))
        await _send_session_opened(ws, session_id, "failed", str(exc))
        return

    opened_at = time.monotonic()
    session.register(
        session.SessionState(
            session_id=session_id,
            conn=conn,
            reservation=reservation,
            memory_bytes=reservation.memory_bytes,
            threads=reservation.threads,
            opened_at=opened_at,
            last_active_at=opened_at,
        )
    )
    await _send_session_opened(ws, session_id, "open")


async def _handle_exec_statement(ws, payload: dict, results_dir: Path) -> None:
    """Run one statement on a held session connection and reply with QUERY_DONE.

    Statements reuse the query id / QUERY_DONE plumbing so their results page
    through the identical fetch pipeline as ordinary queries. One statement runs
    at a time per session (the session lock)."""
    session_id = payload["session_id"]
    statement_id = payload["query_id"]
    sql = payload["sql"]
    timeout_s = min(float(payload.get("timeout_s", 600.0)), settings.max_timeout_s)

    # Ack before anything that can block (the session lock) or fail, so the ack
    # means "this frame arrived" and nothing else. Without it a lost frame is
    # indistinguishable from a slow statement and the row stays queued forever.
    await _send_statement_ack(ws, statement_id)

    state = session.get(session_id)
    if state is None:
        await _send_failed(ws, statement_id, "session not found")
        _in_flight.pop(statement_id, None)
        return
    state.touch()

    result_path = results_dir / f"{statement_id}.parquet"
    try:
        async with state.lock:
            stats = await run_statement(
                sql,
                result_path,
                timeout_s,
                conn=state.conn,
                memory_bytes=state.memory_bytes,
                threads=state.threads,
                enable_profiling=settings.profiling_enabled,
            )
        done_payload: dict[str, object] = {
            "query_id": statement_id,
            "status": "done",
            "row_count": stats["row_count"],
            "duration_ms": stats["duration_ms"],
            "result_bytes": stats["result_bytes"],
            "result_path": str(result_path) if stats["wrote_result"] else None,
            "profile": stats["profile"],
            "result_schema": stats["result_schema"],
        }
        await ws.send(Frame(type=FrameType.QUERY_DONE, payload=done_payload).model_dump_json())
    except TimeoutError as exc:
        trace.get_current_span().set_status(Status(StatusCode.ERROR, "statement timeout"))
        await _send_failed(ws, statement_id, str(exc))
    except asyncio.CancelledError:
        _in_flight.pop(statement_id, None)
        raise
    except Exception as exc:  # noqa: BLE001 - surface any DuckDB error to the client
        trace.get_current_span().set_status(Status(StatusCode.ERROR, str(exc)))
        await _send_failed(ws, statement_id, str(exc))
    finally:
        _in_flight.pop(statement_id, None)


async def _handle_close_session(ws, payload: dict, admission: Admission) -> None:
    """Close a held session: drop the connection, free the admission slot, ack."""
    session_id = payload["session_id"]
    await session.remove(session_id, admission)
    await ws.send(
        Frame(
            type=FrameType.SESSION_CLOSED,
            payload={"session_id": session_id, "status": "closed"},
        ).model_dump_json()
    )


async def _traced_open_session(ws, msg: Frame, admission: Admission) -> None:
    with _tracer.start_as_current_span(
        "handle_open_session",
        context=extract_trace_context(msg.trace_context),
        kind=trace.SpanKind.CONSUMER,
        attributes={"duckhaven.session_id": msg.payload.get("session_id", "")},
    ):
        await _handle_open_session(ws, msg.payload, admission)


async def _traced_exec_statement(ws, msg: Frame, results_dir: Path) -> None:
    with _tracer.start_as_current_span(
        "handle_exec_statement",
        context=extract_trace_context(msg.trace_context),
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "duckhaven.session_id": msg.payload.get("session_id", ""),
            "duckhaven.statement_id": msg.payload.get("query_id", ""),
        },
    ):
        await _handle_exec_statement(ws, msg.payload, results_dir)


async def _handle_dispatch(ws, payload: dict, results_dir: Path, admission: Admission) -> None:
    query_id = payload["query_id"]
    sql = payload["sql"]
    timeout_s = min(float(payload.get("timeout_s", 600.0)), settings.max_timeout_s)
    catalogs = payload.get("catalogs") or []
    active_catalog = payload.get("active_catalog")
    # Back-compat: a pre-multi-catalog control plane sends a single `workspace`
    # (slug == Polaris name) + `backend`; adapt it to a one-element catalog list.
    if not catalogs and isinstance(payload.get("workspace"), dict):
        ws = payload["workspace"]
        slug = ws.get("slug")
        catalogs = [
            {
                "slug": slug,
                "polaris_name": slug,
                "backend": payload.get("backend") or {},
                "default_schema": ws.get("default_schema"),
            }
        ]
        active_catalog = slug
    # Polaris connection info comes from agent config, not the wire; DuckDB
    # does the OAuth2 exchange itself and Polaris vends storage creds on attach.
    polaris = {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }
    stats_for = payload.get("stats_for")
    health_for = payload.get("health_for")
    result_path = results_dir / f"{query_id}.parquet"

    # Admission gate: wait in the FIFO queue until the agent has capacity. While
    # queued we send no QUERY_PROGRESS, so the control plane keeps the query in
    # the `queued` state until it actually starts running.
    #
    # In the `auto` profile we open+attach a connection and run EXPLAIN BEFORE
    # acquiring, so the reservation is sized from the optimizer's estimate; that
    # same connection is then reused for execution + profiling. Static profiles
    # take a fixed ladder slot and let the runner open its own connection.
    conn = None
    try:
        if admission.is_auto:
            conn, estimate = await _prepare_and_estimate(
                sql,
                catalogs=catalogs,
                active_catalog=active_catalog,
                polaris=polaris,
                disabled_filesystems=settings.sandbox_disabled_filesystems,
                lock_config=settings.sandbox_lock_configuration,
            )
            reservation: Reservation = await admission.acquire(_build_request(estimate, admission))
        else:
            reservation = await admission.acquire()
    except QueueFull:
        if conn is not None:
            conn.close()
        trace.get_current_span().set_status(Status(StatusCode.ERROR, "queue full"))
        await _send_failed(ws, query_id, "queue full")
        _in_flight.pop(query_id, None)
        return
    except QueuedTimeout:
        if conn is not None:
            conn.close()
        trace.get_current_span().set_status(Status(StatusCode.ERROR, "queued timeout"))
        await _send_failed(ws, query_id, "queued timeout")
        _in_flight.pop(query_id, None)
        return
    except asyncio.CancelledError:
        # Cancelled while queued: the admission manager dropped the waiter; the
        # control plane already marked the query cancelled.
        if conn is not None:
            conn.close()
        _in_flight.pop(query_id, None)
        raise

    progress = Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": query_id})
    await ws.send(progress.model_dump_json())

    try:
        stats = await run_query(
            sql,
            result_path,
            timeout_s,
            memory_bytes=reservation.memory_bytes,
            threads=reservation.threads,
            catalogs=catalogs,
            active_catalog=active_catalog,
            polaris=polaris,
            stats_for=stats_for,
            health_for=health_for,
            conn=conn,
            enable_profiling=settings.profiling_enabled,
            disabled_filesystems=settings.sandbox_disabled_filesystems,
            lock_config=settings.sandbox_lock_configuration,
        )
        done_payload: dict[str, object] = {
            "query_id": query_id,
            "status": "done",
            "row_count": stats["row_count"],
            "duration_ms": stats["duration_ms"],
            # Only a SELECT writes a Parquet result file; DDL/DML have none.
            "result_path": str(result_path) if stats.get("wrote_result") else None,
            "result_bytes": stats.get("result_bytes"),
            # Normalized post-execution profile (best-effort; null for DDL/DML or
            # when profiling is disabled/failed).
            "profile": stats.get("profile"),
            # The result's column types, read off the DuckDB relation before the
            # Parquet hop (null for DDL/DML). See runner._result_schema.
            "result_schema": stats.get("result_schema"),
        }
        if stats_for:
            done_payload["stats_table"] = {
                "catalog": stats_for.get("catalog"),
                "schema": stats_for.get("schema"),
                "table": stats_for.get("table"),
            }
            done_payload["table_row_count"] = stats.get("table_row_count")
            done_payload["table_size_bytes"] = stats.get("table_size_bytes")
            done_payload["iceberg"] = stats.get("iceberg")
        if health_for:
            done_payload["health"] = stats.get("health")
        done = Frame(type=FrameType.QUERY_DONE, payload=done_payload)
    except TimeoutError:
        trace.get_current_span().set_status(Status(StatusCode.ERROR, "timeout"))
        done = Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": query_id, "status": "failed", "error": "timeout"},
        )
    except Exception as exc:
        span = trace.get_current_span()
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        done = Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": query_id, "status": "failed", "error": str(exc)},
        )
    finally:
        admission.release(reservation)
        _in_flight.pop(query_id, None)

    await ws.send(done.model_dump_json())


async def _push_metrics(ws, sampler: MetricsSampler, admission: Admission) -> None:
    """Push live CPU/memory utilization + admission counts on a fixed cadence, and
    sweep any held session past its idle / max-lifetime lease (the agent-owned
    backstop that reclaims slots orphaned by a lost close or a vanished client)."""
    while True:
        await asyncio.sleep(settings.metrics_sample_interval_s)
        for session_id in await session.sweep_expired(
            admission, settings.session_idle_timeout_s, settings.session_max_lifetime_s
        ):
            # Best-effort: let the control plane flip a still-open row to closed.
            frame = Frame(
                type=FrameType.SESSION_CLOSED,
                payload={"session_id": session_id, "status": "closed", "reason": "agent_self_reap"},
            )
            await ws.send(frame.model_dump_json())
        sample = sampler.sample(
            running_queries=admission.running_count,
            queued_queries=admission.queued_count,
            active_profile=admission.active_profile,
            session_count=session.count(),
        )
        frame = Frame(type=FrameType.METRICS_SAMPLE, payload=sample.model_dump(mode="json"))
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

    # One admission manager for the agent's lifetime; the in-memory queue + the
    # active concurrency profile persist across reconnects (reset on restart).
    admission = Admission(
        profile=settings.max_concurrency_profile,
        headroom=settings.memory_headroom_fraction,
        max_queue_depth=settings.max_queue_depth,
        queued_timeout_s=settings.queued_timeout_s,
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
                auth_payload: dict[str, object] = {
                    "token": auth_token,
                    "name": settings.agent_name or platform.node(),
                    # Where the control plane fetches result Parquet. The host is
                    # normally the socket peer address, observed by the API on
                    # accept; an agent whose inbound address differs from its
                    # egress (e.g. ACI behind a public DNS label) advertises it.
                    "result_port": settings.results_http_port,
                }
                if settings.result_advertise_host:
                    auth_payload["result_host"] = settings.result_advertise_host
                auth = Frame(type=FrameType.AUTH, payload=auth_payload)
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

                # Reconnect reconciliation: the control plane fails this agent's
                # sessions on disconnect (Postgres is the state-of-record), so a
                # resumed socket must not resurrect a stale held connection or leak
                # its admission slot. Drop everything from a prior socket.
                await session.clear_all(admission)

                # Push live utilization on its own cadence; cancelled on disconnect.
                metrics_task = asyncio.create_task(_push_metrics(ws, MetricsSampler(), admission))
                try:
                    await _consume(ws, results_dir, admission)
                finally:
                    metrics_task.cancel()

        except (ConnectionClosed, OSError) as exc:
            logger.warning("Disconnected (%s), reconnecting in 5s", exc)
        await asyncio.sleep(5)


async def _consume(ws, results_dir: Path, admission: Admission) -> None:
    async for raw_msg in ws:
        try:
            msg = Frame.model_validate_json(raw_msg)
        except ValidationError as exc:
            # Never let one bad frame kill the channel: run_control_channel only
            # catches (ConnectionClosed, OSError), so this would escape and stop
            # the reconnect loop entirely.
            logger.warning("Ignoring unparseable frame: %s", exc)
            continue

        # Per-frame receive log: the only evidence that a frame actually arrived.
        # Pairs with the API's post-send log to localize a lost frame to the send
        # or the receive side. Heartbeats are excluded — they are periodic and
        # would bury the frames worth seeing.
        if msg.type != FrameType.HEARTBEAT:
            logger.info("Frame received: %s%s", msg.type, _frame_ref(msg.payload))

        if msg.type == FrameType.HEARTBEAT:
            await ws.send(Frame(type=FrameType.HEARTBEAT).model_dump_json())
            # Re-advertise capabilities so the control plane's stored
            # doc + last_ping_at stay fresh (G-D17-a).
            caps = Frame(type=FrameType.AGENT_STATUS, payload=_get_capabilities().model_dump())
            await ws.send(caps.model_dump_json())

        elif msg.type == FrameType.SET_CONCURRENCY:
            # Agent-global change to the admission slot ladder for FUTURE queries
            # (driven by the worksheet `SET duckhaven_concurrency` command).
            try:
                admission.set_profile(msg.payload["profile"])
                logger.info("Concurrency profile set to %s", admission.active_profile)
            except (KeyError, ValueError) as exc:
                logger.warning("Ignoring invalid SET_CONCURRENCY: %s", exc)

        elif msg.type == FrameType.DISPATCH_QUERY:
            query_id = msg.payload.get("query_id", str(uuid.uuid4()))
            task = asyncio.create_task(_traced_dispatch(ws, msg, results_dir, admission))
            _in_flight[query_id] = task

        elif msg.type == FrameType.CANCEL_QUERY:
            query_id = msg.payload.get("query_id", "")
            task = _in_flight.pop(query_id, None)
            if task:
                task.cancel()

        elif msg.type == FrameType.OPEN_SESSION:
            _spawn(_traced_open_session(ws, msg, admission))

        elif msg.type == FrameType.EXEC_STATEMENT:
            statement_id = msg.payload.get("query_id", str(uuid.uuid4()))
            task = asyncio.create_task(_traced_exec_statement(ws, msg, results_dir))
            _in_flight[statement_id] = task

        elif msg.type == FrameType.CLOSE_SESSION:
            _spawn(_handle_close_session(ws, msg.payload, admission))

        else:
            logger.warning("Ignoring unhandled frame type: %s", msg.type)
