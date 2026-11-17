"""The audit log against real Postgres: append-only, purged only past retention.

The unit suite runs on SQLite, which has neither the trigger that makes the
table append-only nor the advisory lock the purge takes, so both are covered
here against the real thing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from api.models.audit import AuditEvent
from api.services.audit import record
from api.services.audit.retention import _RETENTION_LOCK_KEY, purge_expired

NOW = datetime.now(tz=UTC)


async def _event(db, *, age_days: int = 0) -> AuditEvent:
    event = record(db, "workspace.create", target_name=f"ws-{age_days}")
    event.event_time = NOW - timedelta(days=age_days)
    await db.commit()
    return event


async def test_an_event_carries_its_transaction_id(db_session):
    event = await _event(db_session)

    assert await db_session.scalar(sa.select(AuditEvent.txid).where(AuditEvent.id == event.id))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'user.create'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
async def test_an_event_cannot_be_changed_or_removed(db_session, statement):
    await _event(db_session)

    with pytest.raises(DBAPIError, match="audit_events is append-only"):
        await db_session.execute(sa.text(statement))
    await db_session.rollback()

    assert await db_session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 1


async def test_the_purge_deletes_only_events_past_retention(db_session):
    await _event(db_session, age_days=400)
    kept = await _event(db_session, age_days=10)

    assert await purge_expired(db_session) == 1
    assert (await db_session.scalars(sa.select(AuditEvent.id))).all() == [kept.id]


async def test_the_purge_setting_ends_with_its_transaction(db_session):
    await _event(db_session, age_days=400)
    await purge_expired(db_session)
    await _event(db_session)

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(sa.text("DELETE FROM audit_events"))


async def test_a_replica_that_does_not_hold_the_lock_deletes_nothing(db_session, pg_engine):
    await _event(db_session, age_days=400)

    async with pg_engine.connect() as other:
        await other.execute(sa.text("SELECT pg_advisory_lock(:k)"), {"k": _RETENTION_LOCK_KEY})
        try:
            assert await purge_expired(db_session) == 0
        finally:
            await other.execute(
                sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _RETENTION_LOCK_KEY}
            )

    assert await purge_expired(db_session) == 1
