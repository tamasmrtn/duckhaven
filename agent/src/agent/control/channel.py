import asyncio
import functools
import logging
import platform
import random
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import websockets
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError
from websockets.exceptions import ConnectionClosed

from agent import runtime
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
from agent.executor.estimate_cache import EstimateCache, EstimateKey
from agent.executor.estimator import bucket_for, estimate_memory_bytes
from agent.executor.grant_feedback import GrantFeedback
from agent.executor.runner import (
    AttachMissing,
    _is_single_select,
    apply_memory_limit,
    attach_catalog,
    is_cheap_statement,
    open_and_attach,
    sandbox_state,
)
from agent.executor.supervisor import StatementAbandoned, run_query, run_statement
from agent.metrics.system import (
    MetricsSampler,
    cpu_capability,
    effective_cores,
    effective_memory_bytes,
)
from agent.results import retention
from duckhaven_shared import runtimes
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


@dataclass
class _OpeningSession:
    """An open that has not registered yet, so ``session.remove`` cannot see it.

    ``acquiring`` distinguishes the two halves of an open, which have to be
    stopped differently: while waiting on ``Admission.acquire`` the task holds
    nothing and can simply be cancelled, but once ``_open()`` is on the executor
    a cancel cannot stop that thread and would strand the connection it builds.
    """

    task: asyncio.Task
    acquiring: bool = True


# Opens between their OPEN_SESSION frame and session.register(). Without this a
# CLOSE_SESSION arriving in that window — exactly what the control plane's
# opening deadline produces under load — finds nothing in `session._sessions`,
# frees neither the reservation nor the connection, and silently costs the agent
# that much budget for the rest of its life.
_opening: dict[str, _OpeningSession] = {}

# Opens flagged to clean up after themselves because a CLOSE_SESSION arrived
# while they were already running on the executor.
_abandoned: set[str] = set()


def _make_pool_getter(prefix: str) -> Callable[[], ThreadPoolExecutor]:
    """A lazily-created, cgroup-sized thread pool getter.

    ``effective_cores`` reads the cgroup quota; ``os.cpu_count`` (what the
    interpreter's default pool is sized from) reports the host's cores
    instead — every pool here needs the cgroup-aware count, not the host's.
    """
    pool: ThreadPoolExecutor | None = None

    def get() -> ThreadPoolExecutor:
        nonlocal pool
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=max(2, effective_cores()), thread_name_prefix=prefix
            )
        return pool

    return get


# The pool connection opens run on: session opens, and the one-shot open the
# `auto` profile estimates on. Opens would otherwise land on the interpreter's
# default pool, shared with query execution and with the ``conn.close()`` in
# session teardown — so a burst of opens can queue ahead of the very closes
# that would free capacity for them.
_session_open_executor = _make_pool_getter("dh-open")


# Control-plane protocol features this agent implements, advertised so the API can
# gate on them without a version number (see duckhaven_shared.schemas).
_PROTOCOL_FEATURES = ["statement_ack", "on_demand_attach"]


def _spawn(coro) -> asyncio.Task:
    """Run a handler as a detached task, holding a strong ref until it finishes."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _frame_ref(payload: dict) -> str:
    """The ids that identify a frame in the logs, for correlating with the API."""
    parts = [f" {key}={payload[key]}" for key in ("session_id", "query_id") if payload.get(key)]
    return "".join(parts)


def _get_capabilities() -> AgentCapabilities:
    import duckdb

    conn = duckdb.connect()
    version = duckdb.version()
    # Load the runtime's pre-installed extensions so they are advertised as
    # available; a fresh connection lists only built-ins under `WHERE loaded`.
    # `postgres` loads under that name and reports itself as `postgres_scanner`.
    # An engine no curated runtime matches (a checkout on another DuckDB) is
    # probed for the default runtime's set, which is what a checkout carries.
    baked = runtimes.get(runtime.RUNTIME_ID) or runtimes.RUNTIMES[runtimes.DEFAULT_RUNTIME_ID]
    for ext in baked.extensions:
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
    try:
        # What a fresh connection -- like every one a query runs on -- reads
        # TIMESTAMPTZ values in. The control plane's result cache keys on it.
        timezone = str(conn.execute("SELECT current_setting('TimeZone')").fetchone()[0])
    except Exception:  # noqa: BLE001 - best-effort: an unknown zone only disables caching
        timezone = None
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
        runtime_id=runtime.RUNTIME_ID,
        engine_version=runtime.ENGINE_VERSION,
        agent_version=runtime.APP_VERSION,
        platform=runtime.PLATFORM,
        sandbox=sandbox_state(settings.sandbox_lock_configuration),
        timezone=timezone,
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


# CATALOG_REQUESTs awaiting the control plane's answer, by request id.
_catalog_requests: dict[str, asyncio.Future] = {}
# How long a statement waits for a catalog it turned out to need. The control
# plane answers from its own database, so this only runs out when it is gone.
_CATALOG_REQUEST_TIMEOUT_S = 10.0


async def _request_catalog(ws, query_id: str, slug: str) -> dict | None:
    """Ask the control plane for a catalog's attach descriptor; None if refused."""
    request_id = str(uuid.uuid4())
    answer: asyncio.Future = asyncio.get_running_loop().create_future()
    _catalog_requests[request_id] = answer
    try:
        frame = Frame(
            type=FrameType.CATALOG_REQUEST,
            payload={"request_id": request_id, "query_id": query_id, "catalog": slug},
        )
        await ws.send(frame.model_dump_json())
        payload = await asyncio.wait_for(answer, _CATALOG_REQUEST_TIMEOUT_S)
    except TimeoutError:
        logger.warning("No answer for catalog %s (query %s)", slug, query_id)
        return None
    finally:
        _catalog_requests.pop(request_id, None)
    if payload.get("error"):
        logger.info("Catalog %s refused for query %s: %s", slug, query_id, payload["error"])
    return payload.get("catalog")


def _resolve_catalog_request(payload: dict) -> None:
    answer = _catalog_requests.get(str(payload.get("request_id", "")))
    if answer is not None and not answer.done():
        answer.set_result(payload)


def _attach_missing_for(
    ws, query_id: str, workspace_catalogs: frozenset[str] | None, polaris: dict
) -> AttachMissing | None:
    """How a statement attaches a catalog it needs but was not sent.

    None unless the control plane dispatched on demand, which it signals by
    sending the workspace's catalog slugs: an older control plane has already
    attached them all and has no CATALOG_REQUEST handler. Only those slugs are
    asked for, so a misspelt schema costs no round trip. Runs on the worker
    thread executing the statement, which blocks on the answer.
    """
    if workspace_catalogs is None:
        return None
    loop = asyncio.get_running_loop()

    def attach(conn, name: str) -> bool:
        if name not in workspace_catalogs:
            return False
        request = asyncio.run_coroutine_threadsafe(_request_catalog(ws, query_id, name), loop)
        try:
            descriptor = request.result(timeout=_CATALOG_REQUEST_TIMEOUT_S + 1)
        except Exception as exc:  # noqa: BLE001 - the statement's own error stands
            logger.warning("Requesting catalog %s failed: %s", name, exc)
            return False
        if descriptor is None:
            return False
        attach_catalog(conn, descriptor, polaris=polaris)
        return True

    return attach


def _workspace_catalogs(payload: dict) -> frozenset[str] | None:
    slugs = payload.get("workspace_catalogs")
    return None if slugs is None else frozenset(str(s).lower() for s in slugs)


def _preload(payload: dict) -> dict[str, list[str]]:
    """`open_and_attach` kwargs preparing every kind the workspace has."""
    preload = payload.get("preload") or {}
    return {
        "preload_catalog_kinds": list(preload.get("catalog_kinds") or []),
        "preload_backend_kinds": list(preload.get("backend_kinds") or []),
    }


# Estimates outlive the session that produced them: they depend on the SQL and
# the catalogs, not on who asked. See executor.estimate_cache.
_estimates = EstimateCache(
    ttl_s=settings.estimate_cache_ttl_s, max_entries=settings.estimate_cache_max_entries
)

# What those shapes actually used once they ran, which beats the estimate by 5-11x
# on real queries. Keyed the same way and invalidated alongside it. See
# executor.grant_feedback.
_grants = GrantFeedback(
    ttl_s=settings.estimate_cache_ttl_s,
    max_entries=settings.estimate_cache_max_entries,
    safety=settings.grant_feedback_safety_multiplier,
)

# Estimates currently occupying a worker in that pool, including any abandoned by
# a timeout. The counter goes back down when the worker actually returns, so an
# estimate that was merely slow gives its worker back; one stuck in a spinning
# planner never does.
_estimates_in_flight = 0
# Estimates given up on, cumulative. Reported in METRICS_SAMPLE; one stuck for
# good is a thread and a core lost until the agent restarts, so a rising number
# is worth alerting on.
_estimates_abandoned = 0
# One-shot queries past admission and executing. The admission count also holds
# every open SQL session's reservation, idle or not, so it cannot say how many
# statements are actually running.
_one_shot_running = 0

# The pool EXPLAIN-based estimates run on, kept apart from query execution. Work
# that can block for an unbounded time has no business sharing the interpreter's
# default pool with ``run_statement``. Here it is not merely slow but
# *unkillable* — a spinning DuckDB planner ignores ``interrupt()`` — so an
# abandoned estimate holds its worker forever. Isolating them means the damage
# is bounded to estimation: queries keep executing, and once this pool is used
# up estimation degrades to the fallback bucket instead of queueing behind
# threads that will never return.
_estimate_pool = _make_pool_getter("dh-estimate")


def _estimate_capacity() -> int:
    """Workers in the estimate pool not busy, abandoned work still running included."""
    return max(2, effective_cores()) - _estimates_in_flight


def _estimate_worker_returned(fut: asyncio.Future) -> None:
    """Hand an estimate's worker back once its thread really has returned."""
    global _estimates_in_flight
    _estimates_in_flight -= 1
    if not fut.cancelled():
        fut.exception()  # retrieved, so an abandoned failure is not logged as unhandled


async def _estimate_under_timeout(work, get_conn, *, what: str) -> int | None:
    """Run an EXPLAIN-based estimate, bounded by ``explain_timeout_s``.

    DuckDB's planner can spin inside ``EXPLAIN`` itself — observed twice on TPC-H
    Q08 (an eight-table join) planned against a freshly attached Iceberg catalog,
    pinning a core with the statement never starting, no I/O, and a perfectly
    healthy event loop. A warm connection plans the same query in ~112 ms.

    **``conn.interrupt()`` cannot stop it.** DuckDB honours interrupts while
    processing tuples, not while planning, so a spinning optimizer ignores them —
    measured here as a thread still inside ``conn.execute`` eleven minutes after
    the interrupt. The interrupt is still attempted, because it does work for an
    EXPLAIN that is merely slow, but nothing depends on it: the timeout abandons
    the work and returns. The estimate is then simply unestimable and the caller
    falls back to its default bucket, which is what that bucket is for.

    An abandoned worker is counted as busy until its thread returns, which for a
    spinning planner is never, so estimates run on their own pool and stop being
    attempted once it is used up — see ``_estimate_pool``. One that was merely
    slow hands its worker back when it finishes.

    ``work`` must be the estimate alone. Anything slow before it, such as opening
    and attaching the connection, would count against the timeout and abandon a
    worker that was never stuck.
    """
    global _estimates_in_flight, _estimates_abandoned

    if _estimate_capacity() <= 0:
        logger.warning(
            "Skipping the estimate for %s: all %d estimate workers are busy, some "
            "possibly with spinning planners; falling back",
            what,
            _estimates_in_flight,
        )
        return None

    loop = asyncio.get_running_loop()
    running = True

    def _interrupt() -> None:
        if not running:
            return
        conn = get_conn()
        if conn is None:
            return
        try:
            conn.interrupt()
        except Exception:  # noqa: BLE001 - interrupt is best-effort
            pass

    handle = loop.call_later(settings.explain_timeout_s, _interrupt)
    fut = loop.run_in_executor(_estimate_pool(), work)
    _estimates_in_flight += 1
    try:
        # `wait_for`, not a bare await, because the thread cannot be stopped and
        # so the wait is the only thing that can be abandoned. `shield` keeps the
        # executor future alive past the timeout so the callback above still fires.
        return await asyncio.wait_for(asyncio.shield(fut), settings.explain_timeout_s)
    except TimeoutError:
        _estimates_abandoned += 1
        logger.warning(
            "EXPLAIN estimate for %s exceeded %.1fs and did not stop when interrupted; "
            "abandoning the worker and falling back (%d abandoned so far)",
            what,
            settings.explain_timeout_s,
            _estimates_abandoned,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - estimation must never drop a statement
        logger.warning("Estimate failed for %s: %s", what, exc)
        return None
    finally:
        running = False
        handle.cancel()
        if fut.done():
            _estimates_in_flight -= 1
        else:
            # Abandoned by the timeout or a cancelled caller: the thread keeps
            # running, and its worker is only free again once that thread returns.
            fut.add_done_callback(_estimate_worker_returned)


async def _prepare_and_estimate(sql: str, **attach_kwargs) -> tuple[object | None, int | None]:
    """Open+attach a connection and estimate peak memory (best-effort, `auto`).

    The open runs first, on the connection-open pool and outside the EXPLAIN
    timeout: attach time grows with the workspace's catalogs (about 60 ms per
    DuckLake catalog), and counting it against a 2 s planning budget abandoned
    estimate workers that were never stuck and attached every such query twice.
    Only the EXPLAIN is bounded. Returns `(conn|None, estimate|None)`; a failed
    open returns no connection and leaves `run_query` to open its own. Never
    raises into dispatch.
    """
    # Captured here (event-loop thread, inside handle_dispatch's span) and
    # passed in: run_in_executor does not propagate contextvars to the worker
    # thread, so trace.get_current_span() would see nothing inside the open.
    trace_headers = inject_trace_context()
    loop = asyncio.get_running_loop()
    try:
        conn = await loop.run_in_executor(
            _session_open_executor(),
            functools.partial(open_and_attach, **attach_kwargs, trace_headers=trace_headers),
        )
    except Exception as exc:  # noqa: BLE001 - run_query opens again and reports it
        logger.warning("Opening the connection to estimate on failed: %s", exc)
        return None, None

    estimate = await _estimate_under_timeout(
        lambda: estimate_memory_bytes(conn, sql, safety=settings.estimate_safety_multiplier),
        lambda: conn,
        what="one-shot query",
    )
    return conn, estimate


def _build_request(estimate: int | None, admission: Admission) -> ReservationRequest:
    """Map an estimate (or the fallback bucket) to a reservation request.

    Only the *memory* side comes from the bucket. Threads used to be scaled by the
    same fraction, which floored every bucket below XL to one thread on a 2-core
    agent; see ``Admission.threads_for_statement``.
    """
    if estimate is None:
        frac = BUCKET_FRACTIONS[settings.estimate_fallback_bucket]
        mem = int(frac * admission.budget_bytes)
    else:
        mem, _, _ = bucket_for(estimate, admission.budget_bytes, BUCKET_FRACTIONS)
    return ReservationRequest(memory_bytes=mem, threads=admission.threads_for_statement())


def _elastic_target(admission: Admission, required_bytes: int) -> int:
    """Revocable cache memory to ask for on top of ``required_bytes``.

    Two bounds, and both matter:

    ``elastic_ceiling_fraction`` of the budget, rather than all of it, because
    DuckDB's ``memory_limit`` bounds its own allocations and not the process — the
    agent needs a cushion above the reservation for Python, Arrow buffers and the
    extensions' own memory.

    A **fair share** of the budget, ``budget / live reservations``, because without
    it the first session to ask takes everything that is free and every session
    behind it runs on the bare idle baseline. Measured on a 22-way SF10 burst: one
    session at 2,342 MiB and twenty-one at 64 MiB, where DuckDB spills itself (and
    the container) to death. The share is recomputed on every grant, so it falls as
    sessions arrive and each holder gives the excess back at its next shrink —
    which is what keeps this working without having to reclaim from a connection
    that is busy running a statement.
    """
    ceiling = int(settings.elastic_ceiling_fraction * admission.budget_bytes)
    fair_share = admission.budget_bytes // max(1, admission.running_count)
    return max(0, min(ceiling - required_bytes, fair_share))


def _nobody_can_free_budget(admission: Admission, *, self_is_waiter: bool) -> bool:
    """True when every live consumer is itself parked, so waiting cannot help.

    `session.executing_count()` counts sessions holding their lock — and a waiting
    statement holds its lock for the whole wait, so waiters are indistinguishable
    from executors by that count alone. Subtracting the parked ones (and ourselves)
    is what makes the guard mean what it says; without it a deadlocked agent sees
    ten "executors", none of which will ever release anything, and waits out the
    full timeout instead of running.

    ``one_shot`` covers the queries dispatched outside a session: they hold budget
    and will release it, but are not in the session registry, so a waiter would
    otherwise give up while one was still running.
    """
    others_executing = session.executing_count() - admission.growth_waiting
    if self_is_waiter:
        others_executing -= 1  # we hold our own lock and are not counted as parked yet
    one_shot = admission.running_count - session.count()
    return others_executing <= 0 and one_shot <= 0


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
    """A held session's idle baseline: enough for the attached connection itself.

    Under ``auto`` each statement grows from here to its own estimate and shrinks
    back afterwards (see ``_statement_reservation_request``), so this is a floor
    rather than the memory the session's queries get."""
    budget = admission.budget_bytes
    mem = max(1, min(settings.session_baseline_bytes, budget))
    return ReservationRequest(memory_bytes=mem, threads=admission.threads_for_statement())


def _statement_reservation_request(
    estimate: int | None, admission: Admission, current_bytes: int, sql: str = ""
) -> ReservationRequest | None:
    """Size one session statement from its estimate, or None to keep the current size.

    An unestimable statement falls back to ``estimate_fallback_bucket``, exactly as
    the one-shot path does — ``estimate_memory_bytes`` only estimates single
    SELECTs, so None covers every DDL/DML statement, and those are not cheap.
    An Iceberg ``CREATE TABLE … AS SELECT`` needs a ~76 MiB Parquet row-group
    buffer in one allocation no matter how few rows it writes, so leaving it at the
    idle baseline OOMs it outright. Being more conservative here than the one-shot
    path buys nothing: the fallback is held for the statement and handed straight
    back, the same trade the one-shot path already makes.
    """
    budget = admission.budget_bytes
    if estimate is None and is_cheap_statement(sql):
        # `USE`/`SET` move no data. Charging them the unestimable fallback bucket
        # had every session in a burst claim a third of the agent to run a
        # one-millisecond statement.
        return None
    if estimate is None:
        mem = int(BUCKET_FRACTIONS[settings.estimate_fallback_bucket] * budget)
    else:
        mem, _, _ = bucket_for(estimate, budget, BUCKET_FRACTIONS)
    mem = min(mem, max(1, int(settings.session_max_bucket_fraction * budget)))
    if mem <= current_bytes:
        # Never shrink mid-session on an estimate; the baseline shrink after the
        # statement is what returns the memory, and it does so unconditionally.
        return None
    return ReservationRequest(memory_bytes=mem, threads=admission.threads_for_statement())


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
    workspace_catalogs = _workspace_catalogs(payload)
    # Polaris connection info is vended by the API in the frame (the session
    # credential seam); fall back to agent config for older control planes.
    polaris = payload.get("polaris") or {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }

    request = _session_reservation_request(admission)
    inflight = _opening.get(session_id)
    try:
        try:
            reservation = await admission.acquire(
                request if admission.is_auto else None,
                queued_timeout_s=settings.session_queued_timeout_s,
            )
        except (QueueFull, QueuedTimeout) as exc:
            await _send_session_opened(ws, session_id, "failed", str(exc))
            return
        except asyncio.CancelledError:
            # Abandoned while queued. acquire() has already dropped our waiter
            # and we hold nothing, so there is nothing to release, and
            # _handle_close_session has acked the close.
            raise
        finally:
            # Nothing awaits between here and the _abandoned check below, so a
            # close handler can never read `acquiring` as stale and cancel us
            # once _open() is on the executor, where a cancel cannot help.
            if inflight is not None:
                inflight.acquiring = False

        await admission.apply_pending_resizes()

        if session_id in _abandoned:
            # Closed while queued, but we won the slot before the cancel landed.
            # Hand it straight back rather than opening a connection nobody holds.
            admission.release(reservation)
            return

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
                **_preload(payload),
            )
            # The session's idle slice. Each statement resizes from here to its
            # own required floor plus whatever cache the agent can spare, and
            # back again (see _resize_for_statement / _shrink_to_baseline).
            apply_memory_limit(conn, reservation.total_bytes)
            conn.execute(f"SET threads={reservation.threads}")
            return conn

        try:
            conn = await loop.run_in_executor(_session_open_executor(), _open)
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so the `except Exception` below
            # does not catch it and the reservation would leak on any cancel.
            admission.release(reservation)
            raise
        except Exception as exc:  # noqa: BLE001 - report and release on any open failure
            admission.release(reservation)
            trace.get_current_span().set_status(Status(StatusCode.ERROR, "session open failed"))
            await _send_session_opened(ws, session_id, "failed", str(exc))
            return

        if session_id in _abandoned:
            # The close landed while _open() was on the executor. That thread could
            # not be stopped, so the connection exists and is ours to dispose of:
            # close it and hand the slot back, staying unregistered and silent so
            # the control plane's view (session already failed) stands.
            await _discard_open(conn, session_id, reservation, admission)
            return

        opened_at = time.monotonic()
        active = next((c for c in catalogs if c["slug"] == active_catalog), None) or (
            catalogs[0] if catalogs else {}
        )
        state = session.SessionState(
            session_id=session_id,
            conn=conn,
            reservation=reservation,
            opened_at=opened_at,
            last_active_at=opened_at,
            catalogs=workspace_catalogs or frozenset(c["slug"] for c in catalogs),
            schema=str(active.get("default_schema") or ""),
            workspace_catalogs=workspace_catalogs,
            polaris=polaris,
        )
        session.register(state)
        await _send_session_opened(ws, session_id, "open")
    finally:
        _opening.pop(session_id, None)
        _abandoned.discard(session_id)


async def _discard_open(conn, session_id: str, reservation, admission: Admission) -> None:
    """Throw away a connection whose session was closed before the open finished."""
    loop = asyncio.get_running_loop()
    try:
        # Off the event-loop thread, for the same reason session._teardown is.
        await loop.run_in_executor(_session_open_executor(), conn.close)
    except Exception as exc:  # noqa: BLE001 - close is best-effort
        logger.warning("Closing abandoned session %s connection failed: %s", session_id, exc)
    finally:
        admission.release(reservation)


async def _resize_for_statement(
    state, sql: str, admission: Admission, timeout_s: float = 0.0
) -> EstimateKey | None:
    """Grow a session's reservation to fit the statement it is about to run.

    The session holds only its idle baseline between statements, so this is where
    a query actually gets sized — the same EXPLAIN estimate and T-shirt bucket the
    one-shot dispatch path uses, just against the connection the session already
    has attached (no open/attach round trip needed).

    Growth is best-effort by design: ``try_amend`` hands back whatever the budget
    can spare rather than blocking, because the session is already holding memory
    while asking for more and a blocking wait could deadlock two growing sessions
    against each other. A partial grant still beats the baseline, and a statement
    that cannot grow at all simply runs as it would have before.

    On top of the required floor the statement takes a revocable **elastic**
    grant of whatever budget is idle. That is what pays for DuckDB's external
    file cache, without which every Iceberg scan re-reads its Parquet from object
    storage — and it costs other tenants nothing, because admission takes it back
    the moment someone needs the bytes.

    Returns the key this statement was sized under, so the caller can record what
    it actually used against the same shape (see ``grant_feedback``). ``None``
    when nothing was sized and there is nothing to learn.
    """
    if not admission.is_auto:
        return None
    if not _is_single_select(sql) and not is_cheap_statement(sql):
        # DDL/DML can change what a plan would bind to, so every remembered
        # estimate is now suspect — cheaper and far easier to reason about than
        # tracking which tables a plan touched. `USE`/`SET` are excluded on
        # purpose: they change the binding *context*, which is already part of
        # the key, and a client that opens each session with a `USE` would
        # otherwise clear the cache before it could ever be used.
        _estimates.invalidate_all()
        _grants.invalidate_all()

    key = EstimateKey(catalogs=frozenset(state.catalogs), schema=state.schema, sql=sql)
    hit, estimate = _estimates.get(key)
    if not hit:
        # Bounded by the same EXPLAIN timeout the one-shot path uses. Without it a
        # planner that spins takes the session with it: the statement never starts,
        # a core burns, and nothing times out because the statement's own timeout
        # only covers execution.
        estimate = await _estimate_under_timeout(
            lambda: estimate_memory_bytes(
                state.conn, sql, safety=settings.estimate_safety_multiplier
            ),
            lambda: state.conn,
            what=f"session {state.session_id}",
        )
        _estimates.put(key, estimate)

    if settings.grant_feedback_enabled:
        # A measurement beats the plan wherever one exists. Nothing is needed here
        # to protect the statements that must *not* be sized this way, because
        # only a materialized SELECT is ever profiled and so only a materialized
        # SELECT is ever recorded (see `_record_grant_feedback`): DDL/DML has
        # already dropped the entry on the line above, and `USE`/`SET` never had
        # one. That leaves a SELECT whose EXPLAIN failed as the sole shape that is
        # unestimable *and* measured — and for that one a real peak is a better
        # size than `estimate_fallback_bucket`, which is a guess for DDL.
        measured = _grants.suggest(key)
        if measured is not None:
            estimate = measured

    request = _statement_reservation_request(
        estimate, admission, state.reservation.memory_bytes, sql
    )

    async def _size_once() -> None:
        # Hand the previous statement's cache grant back before sizing this one,
        # so the required floor is measured against the budget that is really
        # free. Accounting only — nothing touches this connection until the runner
        # applies the total — so re-granting the same bytes leaves the cache intact.
        admission.revoke_elastic(state.reservation)
        if request is not None:
            admission.try_amend(state.reservation, request)
        admission.grant_elastic(
            state.reservation, _elastic_target(admission, state.reservation.memory_bytes)
        )
        # Any cache reclaimed from other sessions above is still resident in their
        # DuckDB until this lands; drain before we start using the bytes ourselves.
        # Excludes our own reservation: a stale self-targeting entry queued while
        # we were idle would otherwise deadlock on our own lock, which we hold
        # for the whole statement — see `apply_pending_resizes`.
        await admission.apply_pending_resizes(exclude=state.reservation)

    wanted = request.memory_bytes if request is not None else state.reservation.memory_bytes
    floor = int(settings.statement_admission_floor_fraction * wanted)
    baseline = _session_reservation_request(admission).memory_bytes
    # Jittered, so a queue of waiters that started together does not expire
    # together: ten simultaneous timeouts once released ten statements at the same
    # instant and took the agent down 33 seconds later.
    ceiling = settings.statement_admission_wait_s * random.uniform(0.85, 1.15)
    deadline = time.monotonic() + min(ceiling, timeout_s)
    started = time.monotonic()

    while True:
        await _size_once()
        if state.reservation.memory_bytes >= floor:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if _nobody_can_free_budget(admission, self_is_waiter=True):
            # Every other consumer is parked too, so nothing will be released and
            # waiting can only burn the deadline. This is the tie-break growth has
            # to have, since every waiter holds memory while asking for more.
            break
        # Give it all back before parking. A waiter that sleeps holding a partial
        # grant is holding exactly what it — and everyone behind it — is waiting
        # for; ten of them once held 100.000% of the budget between them.
        admission.try_amend(state.reservation, _session_reservation_request(admission))
        admission.revoke_elastic(state.reservation)
        # `apply_resize`, not `resize_when_free`: we are inside the session's own
        # lock. Dropping the connection too is the point — a parked session has no
        # business holding file cache other statements need.
        await state.apply_resize(state.reservation.total_bytes)
        if not await admission.await_growth(max(0, floor - baseline), remaining):
            # Timed out, or the watchdog released us because nothing was left to
            # wait for. Either way there is no point going round again — but we
            # gave our whole grant back before parking, so leaving now would run
            # the statement on the bare idle baseline. Take back whatever is free
            # first: failing to reach the *floor* is not a reason to execute on
            # 64 MiB when more than that is sitting unused.
            #
            # Measured: one q21 in a 22-way SF10 burst waited 26s here, exited on
            # this path, and died with "failed to allocate 16.0 KiB (63.8 MiB/64.0
            # MiB used)" — the sole failure left in 792 statements once object-store
            # connection reuse was fixed.
            await _size_once()
            break

    state.admission_wait_ms = (time.monotonic() - started) * 1000
    if state.admission_wait_ms >= 1.0:
        logger.info(
            "Statement on session %s waited %.0f ms for budget (granted %d of %d bytes)",
            state.session_id,
            state.admission_wait_ms,
            state.reservation.memory_bytes,
            wanted,
        )
    # `state.memory_bytes`/`.threads` derive live from `state.reservation`, so
    # the runner picks up this total automatically when it runs the statement.
    return key


async def _shrink_to_baseline(state, admission: Admission) -> None:
    """Return a session to its idle baseline once its statement is done.

    Unconditional, and on every exit path: without it one heavy query would pin a
    large *required* reservation for the rest of the session's life, which is
    exactly the starvation `auto` exists to prevent.

    The cache grant survives — it is the DuckDB file cache, and dropping it between
    statements would make the next one re-read every Parquet file it just read. It
    stays revocable, so an idle session holding cache never blocks anyone.

    It is re-granted against the *baseline*, not simply left where the statement
    left it. The grant is sized as "ceiling minus what this statement required", so
    carrying it over unchanged makes an idle session's cache inversely proportional
    to the weight of the last thing it ran — and at the largest bucket, where
    required already exceeds the ceiling, the grant is zero and the shrink drops
    the connection to the bare 64 MiB baseline, evicting the whole cache after
    every statement. That cost the five heaviest SF10 queries 2.5-5x (q01 measured
    at 4,856 ms/rep against 1,630 ms with the cache kept). Re-granting here returns
    every idle session to the same ceiling regardless of what it just ran.

    The connection is resized here, not just the accounting. Skipping that (which
    is what this function used to do) leaves DuckDB holding the previous
    statement's limit while admission believes the memory is free — the exact
    drift that lets two sessions between them exceed the cgroup.
    """
    if not admission.is_auto:
        return
    admission.try_amend(state.reservation, _session_reservation_request(admission))
    admission.grant_elastic(
        state.reservation, _elastic_target(admission, state.reservation.memory_bytes)
    )
    # `apply_resize`, not `resize_when_free`: this runs inside the statement's own
    # `async with state.lock`, so taking the lock again would deadlock.
    await state.apply_resize(state.reservation.total_bytes)
    # Excludes our own reservation for the same reason `_resize_for_statement`
    # does: the resize above already applied our final size directly, so a
    # stale self-targeting pending entry here is redundant and would deadlock
    # on our own lock via `resize_when_free`.
    await admission.apply_pending_resizes(exclude=state.reservation)


def _record_grant_feedback(key: EstimateKey, profile: dict | None) -> None:
    """Teach the next statement of this shape what this one used.

    Caller has already established that the peak is this statement's own rather
    than a watermark delta. Everything else is read from the profile, which is
    absent when profiling is off or capture failed, and for any statement that
    wrote no result. That absence is what keeps DDL/DML and ``USE``/``SET`` out of
    the store entirely, which is in turn why the read side needs no guard of its
    own — nothing that must be sized from the fallback bucket is ever measured.
    """
    if not profile:
        return
    summary = profile.get("summary") or {}
    peak = int(summary.get("peak_memory_bytes") or 0)
    granted = int(summary.get("reserved_memory_bytes") or 0)
    _grants.observe(
        key,
        peak_bytes=peak,
        granted_bytes=granted,
        spilled=bool(summary.get("spill_bytes")),
    )


async def _attach_for_statement(state, descriptors: list[dict]) -> None:
    """Attach the statement's catalogs the session does not have yet.

    Best-effort, like the attach at open: a catalog that fails here fails the
    statement that reads it with DuckDB's own error, not every statement. On
    the open pool because a DuckLake attach is a Postgres round trip.
    """

    def _attach() -> None:
        for descriptor in descriptors:
            try:
                attach_catalog(state.conn, descriptor, polaris=state.polaris)
            except Exception as exc:  # noqa: BLE001 - see docstring
                logger.warning("ATTACH failed for catalog %s: %s", descriptor.get("slug"), exc)

    await asyncio.get_running_loop().run_in_executor(_session_open_executor(), _attach)


async def _handle_exec_statement(
    ws, payload: dict, results_dir: Path, admission: Admission
) -> None:
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
            # The catalogs this statement names that the session has not attached
            # yet. Before the estimate, which has to bind against them too.
            if descriptors := payload.get("catalogs"):
                await _attach_for_statement(state, descriptors)
            # Inside the lock: the size applies to this statement only, and the
            # session runs one statement at a time.
            estimate_key = await _resize_for_statement(state, sql, admission, timeout_s)
            # The statement starts executing now: past the session lock and any
            # wait for memory to grow into. The control plane stamps running_at
            # from this, so its wait/run split and its concurrency see the run
            # while it is in progress rather than only once it has finished.
            await ws.send(
                Frame(
                    type=FrameType.QUERY_PROGRESS, payload={"query_id": statement_id}
                ).model_dump_json()
            )
            # `peak_memory_bytes` is only this statement's own peak if nothing has
            # raised the connection's watermark yet; after that the runner reports
            # a delta (see `runner._apply_watermarks`). Read it before the run,
            # because the run is what moves it.
            #
            # Tracks profiled statements only, so a DDL earlier in the session can
            # leave DuckDB's real watermark above this dict's. The peak then reads
            # high rather than low, which over-grants — the direction that was
            # already the status quo — and the DDL has invalidated the shape's
            # feedback anyway, so it cannot compound.
            peak_is_this_statements = not state.watermarks.get("peak_memory_bytes")
            # A statement that waited for admission budget already spent part of
            # its declared timeout doing so; without subtracting that, it gets a
            # second, fresh full window here, and can run up to 2x its declared
            # timeout under exactly the saturated-agent load this PR targets.
            remaining_timeout_s = max(0.0, timeout_s - state.admission_wait_ms / 1000)
            abandoned = False
            try:
                stats = await run_statement(
                    sql,
                    result_path,
                    remaining_timeout_s,
                    conn=state.conn,
                    memory_bytes=state.memory_bytes,
                    threads=state.threads,
                    enable_profiling=settings.profiling_enabled,
                    watermarks=state.watermarks,
                    admission_wait_ms=state.admission_wait_ms,
                    attach_missing=_attach_missing_for(
                        ws, statement_id, state.workspace_catalogs, state.polaris
                    ),
                )
            except StatementAbandoned:
                # The executor worker never returned -- it may still be running
                # against `state.conn` on its own thread. Nothing below this
                # point may touch that connection again: skip the shrink and
                # the schema refresh (both would race the orphaned thread), and
                # let the outer handler discard the session instead of
                # returning it to the pool of reusable connections.
                abandoned = True
                raise
            finally:
                if not abandoned:
                    await _shrink_to_baseline(state, admission)
            # Only on the success path: `stats` is unbound if the statement raised,
            # and a statement that failed measured nothing worth remembering.
            if estimate_key is not None and peak_is_this_statements:
                _record_grant_feedback(estimate_key, stats["profile"])
            # What the connection now resolves names and reads values in, read by
            # the runner on the statement's own thread (see run_statement_sync).
            # The control plane's result cache keys a session's next statements on
            # it; reported by the engine, so a `USE` that failed half-way can never
            # be mistaken for one that moved. It used to be a separate job behind
            # the estimate pool's capacity guard, which a burst of sessions filled:
            # the report was then left out and the next lookup keyed on a stale
            # schema.
            context = stats.get("session_context")
            if context is not None:
                # `USE` moves what unqualified names bind to, which is part of the
                # estimate cache key.
                state.schema = context["schema"]
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
        if context is not None:
            done_payload["session_context"] = context
        await ws.send(Frame(type=FrameType.QUERY_DONE, payload=done_payload).model_dump_json())
    except StatementAbandoned as exc:
        # The lock has already been released (the `async with` block above
        # exited via this exception), so the session is safe to remove from
        # the registry now -- but never safe to interrupt/close (see
        # `session.discard_poisoned`).
        trace.get_current_span().set_status(Status(StatusCode.ERROR, "statement abandoned"))
        await session.discard_poisoned(session_id, admission)
        await _send_failed(ws, statement_id, str(exc))
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


def _abandon_open(session_id: str) -> None:
    """Stop an open that has not registered yet, so it frees what it holds.

    Cancel is only safe while the task is waiting on ``Admission.acquire``:
    ``acquire`` drops its own waiter and nothing has been built yet. Once
    ``_open()`` is on the executor a cancel cannot stop that thread, so the open
    is flagged instead and disposes of its own connection when it returns.
    """
    inflight = _opening.get(session_id)
    if inflight is None:
        return
    _abandoned.add(session_id)
    if inflight.acquiring:
        inflight.task.cancel()


async def _handle_close_session(ws, payload: dict, admission: Admission) -> None:
    """Close a held session: drop the connection, free the admission slot, ack.

    A session only enters `session._sessions` once its open has finished, so a
    close arriving before that has to reach the in-flight open instead — see
    `_opening`.
    """
    session_id = payload["session_id"]
    if not await session.remove(session_id, admission):
        _abandon_open(session_id)
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


async def _traced_exec_statement(ws, msg: Frame, results_dir: Path, admission: Admission) -> None:
    with _tracer.start_as_current_span(
        "handle_exec_statement",
        context=extract_trace_context(msg.trace_context),
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "duckhaven.session_id": msg.payload.get("session_id", ""),
            "duckhaven.statement_id": msg.payload.get("query_id", ""),
        },
    ):
        await _handle_exec_statement(ws, msg.payload, results_dir, admission)


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
    maintain_for = payload.get("maintain_for")
    result_path = results_dir / f"{query_id}.parquet"
    # On-demand attach: `catalogs` holds only what the statement names, and the
    # rest of the workspace is prepared for and fetched if a view needs it.
    preload = _preload(payload)
    attach_missing = _attach_missing_for(ws, query_id, _workspace_catalogs(payload), polaris)

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
                **preload,
            )
            wait_started = time.monotonic()
            reservation: Reservation = await admission.acquire(_build_request(estimate, admission))
            # One-shot dispatch is the module docstring's own motivating
            # scenario (a cold object-storage scan re-reading its Parquet with
            # no file cache) — without this it got none of the elastic top-up
            # the PR built to fix exactly that, unlike a held session's
            # statements. The grant is fully returned on `release()` below
            # when the query finishes, same as the required-bytes tier already
            # is for one-shot queries today.
            admission.grant_elastic(
                reservation, _elastic_target(admission, reservation.memory_bytes)
            )
        else:
            wait_started = time.monotonic()
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

    # Time spent in the admission queue only: the EXPLAIN estimate before it is
    # planning, not waiting for capacity.
    admission_wait_ms = (time.monotonic() - wait_started) * 1000
    await admission.apply_pending_resizes()

    progress = Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": query_id})
    await ws.send(progress.model_dump_json())

    global _one_shot_running
    _one_shot_running += 1
    try:
        stats = await run_query(
            sql,
            result_path,
            timeout_s,
            # `total_bytes`, not `memory_bytes`: the latter is only the required
            # floor. Without the elastic top-up folded in here, fix 5's
            # `grant_elastic` call above would be accounted for but never
            # actually applied to the connection's DuckDB `memory_limit`.
            memory_bytes=reservation.total_bytes,
            threads=reservation.threads,
            catalogs=catalogs,
            active_catalog=active_catalog,
            polaris=polaris,
            stats_for=stats_for,
            health_for=health_for,
            maintain_for=maintain_for,
            conn=conn,
            enable_profiling=settings.profiling_enabled,
            disabled_filesystems=settings.sandbox_disabled_filesystems,
            lock_config=settings.sandbox_lock_configuration,
            admission_wait_ms=admission_wait_ms,
            attach_missing=attach_missing,
            **preload,
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
            # Only one of these is ever set, named after the probed format.
            done_payload["iceberg"] = stats.get("iceberg")
            done_payload["ducklake"] = stats.get("ducklake")
        if maintain_for:
            done_payload["maintenance"] = stats.get("maintenance")
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
        _one_shot_running -= 1
        admission.release(reservation)
        _in_flight.pop(query_id, None)

    await admission.apply_pending_resizes()
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
        # Backstop for the reclaim path: every async admission site drains its own
        # pending resizes, but a drop here bounds the window in which admission has
        # freed bytes DuckDB is still holding to one sampling interval, whatever
        # path reclaimed them.
        await admission.apply_pending_resizes()
        # Deadlock watchdog. A statement checks before parking whether anything is
        # left to free budget, but the agent can go quiet *after* it parks — and a
        # parked statement re-evaluates nothing. That is how ten of them once sat
        # for 255 seconds on an idle agent. Release the oldest, one per tick: it
        # runs, finishes, and frees enough for the next.
        if admission.growth_waiting and _nobody_can_free_budget(admission, self_is_waiter=False):
            if admission.release_growth_head():
                logger.warning(
                    "Released a statement waiting for budget: %d parked, nothing running",
                    admission.growth_waiting + 1,
                )
        # A session statement parked for memory holds its lock but is not running.
        executing_sessions = session.executing_count()
        sample = sampler.sample(
            running_queries=admission.running_count,
            queued_queries=admission.queued_count,
            active_profile=admission.active_profile,
            session_count=session.count(),
            growth_waiting=admission.growth_waiting,
            estimates_abandoned=_estimates_abandoned,
            executing_queries=_one_shot_running
            + max(0, executing_sessions - admission.growth_waiting),
            idle_sessions=session.count() - executing_sessions,
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

    # One sampler for the agent's lifetime, so its memory-peak poll is started once
    # rather than per connection; rebased on every reconnect.
    sampler = MetricsSampler(memory_poll_interval_s=settings.metrics_memory_poll_interval_s)

    # One admission manager for the agent's lifetime; the in-memory queue + the
    # active concurrency profile persist across reconnects (reset on restart).
    admission = Admission(
        profile=settings.max_concurrency_profile,
        headroom=settings.memory_headroom_fraction,
        max_queue_depth=settings.max_queue_depth,
        queued_timeout_s=settings.queued_timeout_s,
        # Documented as operator-tunable but never passed until now, so setting
        # either env var did nothing. They happened to match Admission's own
        # defaults, which is why it went unnoticed — but the floor now bounds
        # every session's idle baseline, so it has to be honoured.
        floor_bytes=settings.estimate_floor_bytes,
        ceiling_fraction=settings.estimate_ceiling_fraction,
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
                sampler.rebase()
                metrics_task = asyncio.create_task(_push_metrics(ws, sampler, admission))
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
            # Recorded here, not inside the handler: frames are read in order but
            # the handler is a task, so a CLOSE_SESSION read straight after could
            # otherwise be handled before the open task has registered itself.
            session_id = msg.payload.get("session_id")
            task = _spawn(_traced_open_session(ws, msg, admission))
            if session_id:
                _opening[session_id] = _OpeningSession(task=task)

        elif msg.type == FrameType.EXEC_STATEMENT:
            statement_id = msg.payload.get("query_id", str(uuid.uuid4()))
            task = asyncio.create_task(_traced_exec_statement(ws, msg, results_dir, admission))
            _in_flight[statement_id] = task

        elif msg.type == FrameType.CLOSE_SESSION:
            _spawn(_handle_close_session(ws, msg.payload, admission))

        elif msg.type == FrameType.RETAIN_RESULT:
            # The control plane's result cache serves rows from this file: keep it
            # past the retention window until the entry's own expiry.
            retention.retain(
                results_dir,
                str(msg.payload.get("query_id", "")),
                float(msg.payload.get("retain_until") or 0),
            )

        elif msg.type == FrameType.RELEASE_RESULT:
            retention.release(results_dir, str(msg.payload.get("query_id", "")))

        elif msg.type == FrameType.CATALOG_RESPONSE:
            _resolve_catalog_request(msg.payload)

        else:
            logger.warning("Ignoring unhandled frame type: %s", msg.type)
