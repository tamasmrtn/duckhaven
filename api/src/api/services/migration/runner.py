"""The catalog-migration runner: a periodic, leader-elected driver.

Each tick it claims the oldest active ``CatalogMigration`` and advances it to a
terminal state via the engine, then sweeps any completed migrations past their
retention window (dropping the retained old Polaris catalog). Coordination mirrors
the scheduler and maintenance scanner: a Postgres advisory lock elects one leader
per tick, so it is safe to run this loop on every replica.

All migration state lives in the database (the migration row + per-table
checkpoints), so a crash or restart resumes from the last committed checkpoint —
there is no in-memory job state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.catalog_migration import CatalogMigration
from api.services.migration import ACTIVE_STATUSES, engine
from api.services.polaris import PolarisClient

logger = logging.getLogger(__name__)

# Cluster-wide advisory-lock key electing the single migration-runner leader each
# tick. Arbitrary constant ('dhcm'); only its uniqueness against the other
# advisory locks matters (scheduler 0x64687371, scanner 0x64687363).
_MIGRATION_LOCK_KEY = 0x6468636D


async def run_cycle(
    session_factory: async_sessionmaker[AsyncSession], polaris: PolarisClient
) -> dict[str, Any]:
    """Advance one active migration, then sweep expired retention windows."""
    async with session_factory() as db:
        migration = (
            await db.execute(
                sa.select(CatalogMigration)
                .where(CatalogMigration.status.in_(ACTIVE_STATUSES))
                .order_by(CatalogMigration.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()

        processed = None
        if migration is not None:
            processed = str(migration.id)
            await engine.process_migration(db, polaris, migration)

        cutoff = datetime.now(tz=UTC) - timedelta(days=settings.migration_retention_days)
        swept = await engine.cleanup_retained(db, polaris, older_than=cutoff)
        return {"status": "ran", "processed": processed, "swept": swept}


@contextlib.asynccontextmanager
async def migration_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide migration lock.

    Mirrors the scheduler's leadership: a Postgres session-level advisory lock
    ensures exactly one replica runs a cycle per tick. On backends without
    advisory locks (SQLite under tests) leadership is always granted."""
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(
                    sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY}
                )
                await db.commit()


async def run_tick(
    session_factory: async_sessionmaker[AsyncSession], polaris: PolarisClient
) -> dict[str, Any]:
    """One runner tick: run a cycle only if this replica wins leadership."""
    async with migration_leadership(session_factory) as is_leader:
        if not is_leader:
            return {"status": "standby"}
        return await run_cycle(session_factory, polaris)


async def migration_loop(
    session_factory: async_sessionmaker[AsyncSession], polaris: PolarisClient
) -> None:
    """Background loop: wake on a fixed tick and advance in-flight migrations.

    Each cycle is wrapped so one bad run never kills the loop. Leadership is
    elected per tick, so it is safe to run this loop on every replica.
    """
    logger.info("Migration runner started (tick %.0fs)", settings.migration_runner_tick_s)
    while True:
        try:
            result = await run_tick(session_factory, polaris)
            if result.get("status") == "ran" and (result.get("processed") or result.get("swept")):
                logger.info("Migration cycle: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("Migration cycle failed: %s", exc)
        await asyncio.sleep(settings.migration_runner_tick_s)
