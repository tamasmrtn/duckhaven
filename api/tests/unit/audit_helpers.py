"""Reading the audit log back in tests."""

from __future__ import annotations

from sqlalchemy import select

from api.models.audit import AuditEvent


async def audit_events(
    db, action: str | None = None, *, category: str | None = None
) -> list[AuditEvent]:
    """Recorded events, oldest first: one action's, one category's, or every one.

    Sign-ins are left out unless asked for by action or category: most tests
    sign in first, and the event they assert is the change that follows.
    """
    stmt = select(AuditEvent).order_by(AuditEvent.event_time, AuditEvent.id)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    elif category is not None:
        stmt = stmt.where(AuditEvent.category == category)
    else:
        stmt = stmt.where(AuditEvent.category != "auth")
    return list((await db.scalars(stmt)).all())


async def actions(db) -> list[str]:
    return [e.action for e in await audit_events(db)]
