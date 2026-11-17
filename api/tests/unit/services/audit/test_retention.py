"""The daily purge deletes events past the retention window and nothing else."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from audit_helpers import audit_events

from api.config import settings
from api.services.audit import record
from api.services.audit.retention import purge_expired

NOW = datetime(2026, 11, 17, tzinfo=UTC)


async def _event_at(db, when: datetime, name: str) -> None:
    record(db, "workspace.create", target_name=name).event_time = when
    await db.commit()


async def test_events_past_the_window_are_deleted(db_session):
    await _event_at(db_session, NOW - timedelta(days=366), "old")
    await _event_at(db_session, NOW - timedelta(days=364), "recent")

    assert await purge_expired(db_session, now=NOW) == 1
    assert [e.target_name for e in await audit_events(db_session)] == ["recent"]


async def test_zero_days_keeps_every_event(db_session, monkeypatch):
    monkeypatch.setattr(settings, "audit_retention_days", 0)
    await _event_at(db_session, NOW - timedelta(days=4000), "ancient")

    assert await purge_expired(db_session, now=NOW) == 0
    assert len(await audit_events(db_session)) == 1
