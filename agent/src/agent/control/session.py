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

    def touch(self) -> None:
        self.last_active_at = time.monotonic()


_sessions: dict[str, SessionState] = {}


def register(state: SessionState) -> None:
    _sessions[state.session_id] = state


def get(session_id: str) -> SessionState | None:
    return _sessions.get(session_id)


def count() -> int:
    return len(_sessions)


def _teardown(state: SessionState, admission: Admission) -> None:
    """Close the held connection and release its admission reservation (both
    best-effort so one failure never strands the other)."""
    try:
        state.conn.close()
    except Exception as exc:  # noqa: BLE001 - close is best-effort
        logger.warning("Closing session %s connection failed: %s", state.session_id, exc)
    admission.release(state.reservation)


def remove(session_id: str, admission: Admission) -> bool:
    """Drop a session, freeing its connection + admission slot. Returns whether a
    session was present."""
    state = _sessions.pop(session_id, None)
    if state is None:
        return False
    _teardown(state, admission)
    return True


def clear_all(admission: Admission) -> None:
    """Tear down every held session (reconnect reconciliation)."""
    for session_id in list(_sessions):
        remove(session_id, admission)


def sweep_expired(admission: Admission, idle_s: float, max_life_s: float) -> list[str]:
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
            if remove(state.session_id, admission):
                reaped.append(state.session_id)
    return reaped
