import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_permission
from api.models.audit import AuditEvent
from api.models.user import User
from api.schemas.audit import AuditEventOut, Category, Outcome
from api.schemas.page import Page
from api.services.audit.record import event_payload
from api.services.audit.registry import ACTIONS
from api.services.paging import paginate
from api.services.permissions import Permission

router = APIRouter()


def _out(event: AuditEvent) -> AuditEventOut:
    spec = ACTIONS.get(event.action)
    return AuditEventOut.model_validate(
        {**event_payload(event), "label": spec.label if spec else event.action}
    )


@router.get("/audit/events", response_model=Page[AuditEventOut])
async def list_audit_events(
    since: datetime | None = Query(default=None, description="Events at or after this time."),
    until: datetime | None = Query(default=None, description="Events before this time."),
    category: Category | None = Query(default=None),
    action: str | None = Query(
        default=None,
        description="An action name, or a prefix ending in `.*` such as `catalog_grant.*`.",
    ),
    actor_id: uuid.UUID | None = Query(
        default=None,
        description="Events this account made, or a delegated call made on its behalf.",
    ),
    subject_id: uuid.UUID | None = Query(
        default=None, description="Events that changed this account's access."
    ),
    workspace: str | None = Query(default=None, description="A workspace slug or id."),
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    outcome: Outcome | None = Query(default=None),
    self_change: bool | None = Query(
        default=None, description="Only changes the actor made to their own access."
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.AUDIT_READ)),
) -> Page[AuditEventOut]:
    """The access audit log, newest first: access changes, sign-ins and refusals.

    Deployment-wide: every workspace's events, for holders of `audit:read`.
    Events are kept for `AUDIT_RETENTION_DAYS` (365 by default) and never
    changed, and names are kept as they were, so an event about a deleted user
    or workspace still reads in full.
    """
    stmt = select(AuditEvent)
    if since is not None:
        stmt = stmt.where(AuditEvent.event_time >= since)
    if until is not None:
        stmt = stmt.where(AuditEvent.event_time < until)
    if category is not None:
        stmt = stmt.where(AuditEvent.category == category)
    if action is not None:
        if action.endswith(".*"):
            stmt = stmt.where(AuditEvent.action.startswith(action[:-1], autoescape=True))
        else:
            stmt = stmt.where(AuditEvent.action == action)
    if actor_id is not None:
        stmt = stmt.where(
            or_(AuditEvent.actor_id == actor_id, AuditEvent.on_behalf_of_id == actor_id)
        )
    if subject_id is not None:
        stmt = stmt.where(AuditEvent.subject_id == subject_id)
    if workspace is not None:
        try:
            stmt = stmt.where(AuditEvent.workspace_id == uuid.UUID(workspace))
        except ValueError:
            stmt = stmt.where(AuditEvent.workspace_slug == workspace)
    if target_type is not None:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if outcome is not None:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    if self_change is not None:
        stmt = stmt.where(AuditEvent.self_change.is_(self_change))

    rows, next_cursor, has_more = await paginate(
        db,
        stmt,
        sort=[AuditEvent.event_time.desc(), AuditEvent.id.desc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[AuditEventOut](
        items=[_out(r[0]) for r in rows], cursor=next_cursor, has_more=has_more
    )
