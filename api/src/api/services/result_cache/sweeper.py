"""Expiry for the result cache.

A leader-elected background loop (a Postgres advisory lock, like the scheduler
and the session reaper) that deletes entries past their sliding expiry or their
hard maximum age, tells agents they may delete the result files of the
agent-stored ones, re-applies the inline space budgets, and publishes how many
bytes the cache holds.

Lookups already drop an expired or stale entry they come across; this loop is
what removes the ones nobody asks for again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.metrics import record_result_cache_eviction, set_result_cache_bytes
from api.models.result_cache import ResultCacheEntry
from api.services.result_cache.storage import AGENT, INLINE, enforce_inline_quota, release

logger = logging.getLogger(__name__)

# Distinct advisory-lock key in the "dhs" family ('dhrc': result cache).
_SWEEPER_LOCK_KEY = 0x64687263


@contextlib.asynccontextmanager
async def sweeper_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide sweeper lock. On
    backends without advisory locks (SQLite under tests) leadership is granted."""
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _SWEEPER_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _SWEEPER_LOCK_KEY})
                await db.commit()


async def run_cycle(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(tz=UTC)
    async with session_factory() as db:
        expired = (
            await db.scalars(
                sa.select(ResultCacheEntry).where(
                    sa.or_(
                        ResultCacheEntry.expires_at <= now,
                        ResultCacheEntry.hard_expires_at <= now,
                    )
                )
            )
        ).all()
        await release(db, list(expired))
        if expired:
            await db.execute(
                sa.delete(ResultCacheEntry).where(ResultCacheEntry.id.in_([e.id for e in expired]))
            )
            await db.commit()
            record_result_cache_eviction("expired", len(expired))
        evicted = await enforce_inline_quota(db, None)

        held = dict(
            (
                await db.execute(
                    sa.select(
                        ResultCacheEntry.storage, sa.func.sum(ResultCacheEntry.result_bytes)
                    ).group_by(ResultCacheEntry.storage)
                )
            ).all()
        )
        for storage in (INLINE, AGENT):
            set_result_cache_bytes(storage, int(held.get(storage) or 0))
    return {"expired": len(expired), "evicted": evicted}


async def run_tick(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int] | None:
    async with sweeper_leadership(session_factory) as is_leader:
        if not is_leader:
            return None
        return await run_cycle(session_factory)


async def sweeper_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop. Each cycle is wrapped so one bad run never kills the loop;
    leadership is elected per tick, so every replica may run it."""
    logger.info("Result cache sweeper started (tick %.0fs)", settings.result_cache_sweep_interval_s)
    while True:
        try:
            result = await run_tick(session_factory)
            if result and any(result.values()):
                logger.info("Result cache sweep: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("Result cache sweep failed: %s", exc)
        await asyncio.sleep(settings.result_cache_sweep_interval_s)
