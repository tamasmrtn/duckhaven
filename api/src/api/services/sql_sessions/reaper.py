"""Idle / max-lifetime reaper for SQL sessions.

A leader-elected background loop (Postgres advisory lock, mirroring the scheduler
and maintenance scanners) that force-closes sessions which have gone idle past
``sql_session_idle_timeout_s`` or exceeded ``sql_session_max_lifetime_s``. Reaping
dispatches CLOSE_SESSION to the pinned agent (freeing its held connection +
admission slot) and marks the row ``expired`` — so a crashed client never pins an
agent forever. Sessions whose agent is gone are already ``failed`` by the
disconnect reconciler; the reaper only handles live-but-idle/old ones.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.metrics import record_sql_session_closed
from api.models.sql_session import SqlSession
from api.services.sql_sessions.service import dispatch_close_session

logger = logging.getLogger(__name__)

# Distinct advisory-lock key (cf. scheduler 0x64687371, scanner 0x64687363).
_REAPER_LOCK_KEY = 0x64687373


def _aware(value: datetime) -> datetime:
    """Treat a naive timestamp (SQLite under tests strips tzinfo) as UTC so it can
    be compared to the aware cutoffs. On Postgres the value is already aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@contextlib.asynccontextmanager
async def reaper_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide reaper lock. On
    backends without advisory locks (SQLite under tests) leadership is granted."""
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _REAPER_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _REAPER_LOCK_KEY})
                await db.commit()


async def run_cycle(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Close open sessions that are idle or past their max lifetime. Returns counts
    keyed by reap reason."""
    now = datetime.now(tz=UTC)
    idle_cutoff = now - timedelta(seconds=settings.sql_session_idle_timeout_s)
    lifetime_cutoff = now - timedelta(seconds=settings.sql_session_max_lifetime_s)
    opening_cutoff = now - timedelta(seconds=settings.sql_session_opening_deadline_s)

    reaped = {"idle": 0, "max_lifetime": 0, "open_timeout": 0}
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    sa.select(SqlSession).where(
                        sa.or_(
                            sa.and_(
                                SqlSession.status == "open",
                                sa.or_(
                                    SqlSession.last_active_at < idle_cutoff,
                                    SqlSession.created_at < lifetime_cutoff,
                                ),
                            ),
                            # Stuck opening: the agent never acked but may hold a slot.
                            sa.and_(
                                SqlSession.status == "opening",
                                SqlSession.created_at < opening_cutoff,
                            ),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        for session in rows:
            if session.status == "opening":
                reason = "open_timeout"
                session.status = "failed"
                session.error = "open_timeout"
            else:
                reason = "max_lifetime" if _aware(session.created_at) < lifetime_cutoff else "idle"
                session.status = "expired"
                session.error = f"reaped ({reason})"
            session.closed_at = now
            reaped[reason] += 1
        await db.commit()

        # Best-effort: tell the pinned agents to drop the connections + free slots.
        for session in rows:
            if session.agent_id is not None:
                with contextlib.suppress(Exception):
                    await dispatch_close_session(db, session.agent_id, session.id)

    for reason, count in reaped.items():
        for _ in range(count):
            record_sql_session_closed(reason)
    return reaped


async def run_tick(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int] | None:
    async with reaper_leadership(session_factory) as is_leader:
        if not is_leader:
            return None
        return await run_cycle(session_factory)


async def reaper_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop: reap idle/old sessions on a fixed tick. Each cycle is
    wrapped so one bad run never kills the loop; leadership is elected per tick."""
    logger.info(
        "SQL session reaper started (tick %.0fs, idle %.0fs, max %.0fs)",
        settings.sql_session_reaper_tick_s,
        settings.sql_session_idle_timeout_s,
        settings.sql_session_max_lifetime_s,
    )
    while True:
        try:
            result = await run_tick(session_factory)
            if result and any(result.values()):
                logger.info("SQL session reaper cycle: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("SQL session reaper cycle failed: %s", exc)
        await asyncio.sleep(settings.sql_session_reaper_tick_s)
