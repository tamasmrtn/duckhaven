"""Retention for the audit log.

A daily, leader-elected loop deletes events older than ``AUDIT_RETENTION_DAYS``.
The table's trigger refuses every DELETE unless the transaction sets
``duckhaven.audit_purge``, so this is the one writer that removes events, and it
removes only those past the window.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.audit import PURGE_SETTING, AuditEvent

logger = logging.getLogger(__name__)

# Distinct advisory-lock key in the "dhs" family ('dhau': audit).
_RETENTION_LOCK_KEY = 0x64686175
_INTERVAL_S = 24 * 60 * 60.0


async def purge_expired(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete the events past the retention window; return how many.

    One replica does it at a time: the advisory lock is held for the transaction,
    and a replica that does not get it deletes nothing. SQLite (the unit suite)
    has neither the lock nor the trigger, and runs it unguarded.
    """
    if settings.audit_retention_days == 0:
        return 0
    cutoff = (now or datetime.now(tz=UTC)) - timedelta(days=settings.audit_retention_days)
    if db.bind.dialect.name == "postgresql":
        locked = await db.scalar(
            sa.text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _RETENTION_LOCK_KEY}
        )
        if not locked:
            await db.rollback()
            return 0
        await db.execute(sa.text(f"SET LOCAL {PURGE_SETTING} = 'on'"))
    result = await db.execute(sa.delete(AuditEvent).where(AuditEvent.event_time < cutoff))
    await db.commit()
    return result.rowcount


async def retention_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop; a failed purge is logged and retried the next day."""
    logger.info("Audit retention started (%d days)", settings.audit_retention_days)
    while True:
        try:
            async with session_factory() as db:
                deleted = await purge_expired(db)
            if deleted:
                logger.info("Purged %d audit events past retention", deleted)
        except Exception:  # noqa: BLE001 - the loop must survive any purge failure
            logger.exception("Audit retention purge failed")
        await asyncio.sleep(_INTERVAL_S)
