"""Held DuckDB connections for SQL sessions.

A SQL session (opened by the control plane over ``OPEN_SESSION``) holds one
persistent, attached DuckDB connection plus the admission reservation it occupies
for its whole lifetime, so dbt/dlt can run many statements with connection-scoped
state (temp relations, ``USE``, ``SET``). This module is the agent-side registry
of those held connections — the session analogue of ``channel._in_flight``.

The registry is process-global and ephemeral: it is cleared on every reconnect
(``clear_all``) because the control plane treats an agent disconnect as failing
that agent's sessions (Postgres is the state-of-record), so a resumed socket must
never resurrect a stale connection or leak its reservation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import duckdb

from agent.executor import runner
from agent.executor.admission import Admission, Reservation

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """One held session: its attached connection, admission reservation, and the
    resource slice (memory/threads) applied to the connection at open."""

    session_id: str
    conn: duckdb.DuckDBPyConnection
    reservation: Reservation
    memory_bytes: int
    threads: int
    # Lease clocks (monotonic seconds): ``opened_at`` bounds a session's total
    # lifetime; ``last_active_at`` (bumped by ``touch`` on each statement) bounds
    # its idle time. The agent self-expires orphaned sessions from these so a lost
    # CLOSE_SESSION never strands a slot until the next reconnect.
    opened_at: float
    last_active_at: float
    # Serializes statements on the shared connection: a session runs one
    # statement at a time (dbt/dlt use a connection serially).
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # DuckDB's peak memory/temp-dir metrics are high-water marks for the whole
    # connection, so on a held session they carry over between statements. This
    # is what each statement's reported peak/spill is measured against; see
    # executor.runner._apply_watermarks.
    watermarks: dict[str, int] = field(default_factory=dict)
    # What this connection's unqualified names bind against, and therefore part of
    # the estimate cache key: `analytics`, `sf10` and `sf100` all have a
    # `lineitem`, so an estimate is only reusable within the same catalog set and
    # schema. `schema` is refreshed after any statement that can change it.
    catalogs: frozenset[str] = field(default_factory=frozenset)
    schema: str = ""
    # How long the last statement spent waiting for budget before it could run,
    # surfaced in its profile as `admission_wait_ms`. Reset per statement.
    admission_wait_ms: float = 0.0

    def touch(self) -> None:
        self.last_active_at = time.monotonic()

    def is_idle(self) -> bool:
        """Whether the connection is safe to resize right now.

        The lock is held for exactly as long as a statement is in flight, so this
        is the admission manager's guarantee that reclaiming this session's
        elastic memory cannot pull it out from under a running query.
        """
        return not self.lock.locked()

    async def apply_resize(self, total_bytes: int) -> None:
        """Move the connection's DuckDB memory limit to a new total.

        **The caller must hold ``lock``.** The `SET` runs on an executor thread:
        lowering a limit makes DuckDB evict its file cache inline, which is fast
        (~12 ms for 383 MB) but not free, and it has no business happening on the
        event loop while 20-odd other sessions are waiting to be served.

        Failures are logged rather than raised: a limit that does not land leaves
        DuckDB holding *more* than admission accounted, which the session's next
        statement corrects when it sets its own limit.
        """
        self.memory_bytes = total_bytes
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, runner.apply_memory_limit, self.conn, total_bytes)
        except Exception as exc:  # noqa: BLE001 - a stale limit must not fail a session
            logger.warning(
                "Resizing session %s to %d bytes failed: %s", self.session_id, total_bytes, exc
            )

    async def resize_when_free(self, total_bytes: int) -> None:
        """Resize from the outside — the admission manager reclaiming cache.

        Takes the lock first, because unlike ``apply_resize``'s callers this one
        does not own the session and the connection may be mid-statement. Waiting
        is correct: the accounting has already been updated, so the bytes are
        merely late in coming back, and a statement that finishes first hands them
        over itself.
        """
        async with self.lock:
            await self.apply_resize(total_bytes)

    def refresh_schema(self) -> None:
        """Re-read the connection's current schema after something may have moved it.

        `USE` is the only statement that changes it, and it is cheap, so this runs
        after those rather than before every estimate."""
        try:
            row = self.conn.execute("SELECT current_schema()").fetchone()
        except Exception as exc:  # noqa: BLE001 - a stale schema only costs a cache miss
            logger.warning("Reading current_schema for session %s failed: %s", self.session_id, exc)
            return
        if row:
            self.schema = str(row[0])


_sessions: dict[str, SessionState] = {}


def register(state: SessionState) -> None:
    # Hand the admission manager the two things it needs to treat this session's
    # elastic grant as revocable: whether the connection is free right now, and
    # how to shrink it. Without both, `_reclaim_elastic` skips the reservation.
    state.reservation.is_idle = state.is_idle
    state.reservation.on_resize = state.resize_when_free
    _sessions[state.session_id] = state


def get(session_id: str) -> SessionState | None:
    return _sessions.get(session_id)


def count() -> int:
    return len(_sessions)


def executing_count() -> int:
    """Sessions currently running a statement (their lock is held).

    The deadlock guard for the statement admission wait: a statement only waits
    for budget while somebody else is actually executing and will therefore free
    some. If nothing is running, waiting cannot help and would just burn the
    timeout — which is the tie-break `try_amend`'s docstring says growth has to
    have, since every waiter is holding memory while asking for more.
    """
    return sum(1 for state in _sessions.values() if state.lock.locked())


async def _teardown(state: SessionState, admission: Admission) -> None:
    """Interrupt any statement running on this connection, wait for it to unwind
    (via the session's own lock — never by racing close() against it), then close
    off the event-loop thread and release the reservation.

    All three steps are best-effort with respect to each other: interrupt/close
    failures are logged, not raised, and the reservation is always released even
    if this task is itself cancelled mid-teardown (e.g. while awaiting the lock)."""
    loop = asyncio.get_running_loop()
    # Unhooked first, before anything else: `is_idle()` reports True until the
    # lock below is actually acquired, so a reclaim could otherwise queue a
    # resize for this reservation in the gap between interrupt() and taking the
    # lock, landing after the connection is closed. Clearing these makes the
    # reservation immediately ineligible as a reclaim target for the rest of
    # teardown, closing that window rather than merely tolerating it.
    state.reservation.is_idle = None
    state.reservation.on_resize = None
    try:
        try:
            # Thread-safe (same mechanism as executor.supervisor's timeout path):
            # unwinds any in-flight statement now instead of letting close() wait
            # for it to run to completion.
            state.conn.interrupt()
        except Exception as exc:  # noqa: BLE001 - interrupt is best-effort
            logger.warning("Interrupting session %s connection failed: %s", state.session_id, exc)
        # _handle_exec_statement holds this lock for one statement and releases
        # it on every exit path (success/timeout/cancel). Awaiting it here means
        # close() never races a still-unwinding statement ("Connection already
        # closed!"). Awaiting a lock never blocks the event loop, so other
        # sessions/heartbeats keep running while we wait.
        async with state.lock:
            try:
                # Off the event-loop thread: even a slow close() can't stall it.
                await loop.run_in_executor(None, state.conn.close)
            except Exception as exc:  # noqa: BLE001 - close is best-effort
                logger.warning("Closing session %s connection failed: %s", state.session_id, exc)
    finally:
        admission.release(state.reservation)
        # Releasing frees budget, which can promote a waiter and reclaim cache from
        # somebody else; that resize is queued, not applied, so drain it here.
        await admission.apply_pending_resizes()


async def remove(session_id: str, admission: Admission) -> bool:
    """Drop a session, freeing its connection + admission slot. Returns whether a
    session was present."""
    state = _sessions.pop(session_id, None)
    if state is None:
        return False
    await _teardown(state, admission)
    return True


async def discard_poisoned(session_id: str, admission: Admission) -> bool:
    """Drop a session whose connection may still be in use by an abandoned
    executor worker (``supervisor.StatementAbandoned``), without touching it.

    Unlike ``remove``/``_teardown``, this never calls ``conn.interrupt()`` or
    ``conn.close()`` — a worker abandoned to a spinning DuckDB planner keeps
    running against the connection indefinitely, on its own thread, and
    calling into that same connection from another thread while it does would
    race it. So the connection is simply never touched again: the Python
    reference is dropped (the object itself stays alive as long as the
    orphaned thread's stack still references it) and only the accounting
    (reservation, registry entry) is cleaned up. One leaked connection per
    incident is the deliberately bounded cost of never letting one poisoned
    connection take the whole agent down with it.
    """
    state = _sessions.pop(session_id, None)
    if state is None:
        return False
    # Unhook first, same reasoning as `_teardown`: a reclaim must never try to
    # resize a connection nobody will ever touch safely again.
    state.reservation.is_idle = None
    state.reservation.on_resize = None
    admission.release(state.reservation)
    await admission.apply_pending_resizes()
    return True


async def clear_all(admission: Admission) -> None:
    """Tear down every held session (reconnect reconciliation), concurrently.

    This runs before the new socket starts consuming frames (heartbeats
    included), so serializing several stale sessions — each possibly waiting out
    an interrupt — would reproduce the same head-of-line stall a single slow
    close now avoids, just at reconnect time instead. Gathering bounds total
    reconciliation time by the slowest single teardown rather than their sum."""
    await asyncio.gather(
        *(remove(session_id, admission) for session_id in list(_sessions)),
        return_exceptions=True,
    )


async def sweep_expired(admission: Admission, idle_s: float, max_life_s: float) -> list[str]:
    """Tear down held sessions past their idle or max-lifetime lease, freeing the
    connection + admission slot. Returns the reaped session ids.

    This is the agent-owned backstop: even if a CLOSE_SESSION is lost or a client
    exits without closing, the slot is reclaimed here rather than pinned until the
    next reconnect. Timeouts <= 0 disable the respective check. Sessions mid-statement
    (``lock`` held) are skipped and revisited on a later sweep so a connection is
    never closed out from under a running statement. Teardown funnels through
    ``remove`` (dict-pop guarded), so a reservation is released at most once."""
    now = time.monotonic()
    reaped: list[str] = []
    for state in list(_sessions.values()):
        if state.lock.locked():
            continue
        idle_expired = idle_s > 0 and now - state.last_active_at > idle_s
        life_expired = max_life_s > 0 and now - state.opened_at > max_life_s
        if idle_expired or life_expired:
            if await remove(state.session_id, admission):
                reaped.append(state.session_id)
    return reaped
