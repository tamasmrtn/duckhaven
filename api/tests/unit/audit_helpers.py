"""Reading the audit log back in tests."""

from __future__ import annotations

from sqlalchemy import select

from api.models.audit import AuditEvent


async def audit_events(db, action: str | None = None) -> list[AuditEvent]:
    """Every recorded event, oldest first, optionally only one action's."""
    stmt = select(AuditEvent).order_by(AuditEvent.event_time, AuditEvent.id)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    return list((await db.scalars(stmt)).all())


async def actions(db) -> list[str]:
    return [e.action for e in await audit_events(db)]
