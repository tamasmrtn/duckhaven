"""Write audit events.

:func:`record` adds an event to the caller's transaction, so the event commits
or rolls back with the change it describes. Call it immediately before the
commit that persists the change, after any point that can still roll back.
Each committed event is also written as one JSON line to the
``duckhaven.audit`` logger, for a SIEM; a rolled-back one never is.

:func:`record_now` writes an event in a transaction of its own, for an outcome
whose request rolls back: a denial, a failed sign-in. Repeats of the same
event from the same source within a minute are dropped, so a flood of refused
requests cannot turn into a flood of writes.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from api.models.audit import AuditEvent
from api.services.audit.context import actor_type_of, current
from api.services.audit.registry import ACTIONS

#: The SIEM stream: one JSON object per committed event.
audit_logger = logging.getLogger("duckhaven.audit")
logger = logging.getLogger(__name__)

_PENDING = "duckhaven.audit.pending"
_LISTENING = "duckhaven.audit.listening"

# A key naming a secret has no business in an event; refusing it at the source
# keeps a careless `details=` from ever reaching the table or the log.
_FORBIDDEN_KEY = re.compile(r"token|secret|password|hash|private_key", re.IGNORECASE)

TEXT_MAX = 2048
_DEDUPE_WINDOW_S = 60.0
_DEDUPE_MAX = 10_000
_recent: dict[tuple[str, ...], float] = {}


def clip(value: str | None, limit: int = TEXT_MAX) -> str | None:
    """``value`` cut to ``limit`` characters, marked when cut."""
    if value is None or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _check_keys(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if _FORBIDDEN_KEY.search(str(key)):
                raise ValueError(f"audit {where} may not carry {key!r}")
            _check_keys(inner, where)
    elif isinstance(value, list | tuple):
        for inner in value:
            _check_keys(inner, where)


def _jsonable(value: Any) -> Any:
    """``value`` with UUIDs and datetimes as strings, as JSONB stores them."""
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def _emit(session: Session) -> None:
    for payload in session.info.pop(_PENDING, []):
        audit_logger.info(json.dumps(payload, default=str, separators=(",", ":")))


def _drop(session: Session) -> None:
    session.info.pop(_PENDING, None)


def _listen(db: AsyncSession) -> None:
    sync = db.sync_session
    if sync.info.get(_LISTENING):
        return
    sa.event.listen(sync, "after_commit", _emit)
    sa.event.listen(sync, "after_rollback", _drop)
    sync.info[_LISTENING] = True


def record(
    db: AsyncSession,
    action: str,
    *,
    target_id: object | None = None,
    target_name: str | None = None,
    workspace: Any = None,
    subject: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    details: dict | None = None,
    outcome: str = "success",
    error_code: str | None = None,
    actor: Any = None,
    actor_type: str | None = None,
) -> AuditEvent:
    """Add one event for ``action`` to ``db``'s transaction and return it.

    ``workspace`` is a :class:`Workspace` (its id and slug are kept), ``subject``
    the :class:`User` whose access changed, and ``actor`` the user who acted when
    the request's authenticated caller is not it (a sign-in, an identity
    provider). Without ``actor`` it is the request's caller, or the system
    outside a request.
    """
    spec = ACTIONS[action]
    for where, value in (("before", before), ("after", after), ("details", details)):
        _check_keys(value, where)

    ctx = current()
    on_behalf_of_id = on_behalf_of_email = None
    auth_method = credential_id = None
    if actor is not None:
        actor_id, actor_email = actor.id, actor.email
        kind = actor_type or actor_type_of(actor)
    elif ctx is not None and ctx.actor_id is not None:
        actor_id, actor_email, kind = ctx.actor_id, ctx.actor_email, actor_type or ctx.actor_type
        on_behalf_of_id, on_behalf_of_email = ctx.on_behalf_of_id, ctx.on_behalf_of_email
        auth_method, credential_id = ctx.auth_method, ctx.credential_id
    else:
        actor_id = actor_email = None
        kind = actor_type or ("anonymous" if ctx is not None else "system")
        auth_method = None if ctx is not None else "system"

    subject_id = getattr(subject, "id", None)
    event = AuditEvent(
        id=uuid.uuid4(),
        event_time=datetime.now(tz=UTC),
        event_version=1,
        category=spec.category,
        action=action,
        outcome=outcome,
        actor_id=actor_id,
        actor_email=actor_email,
        actor_type=kind,
        on_behalf_of_id=on_behalf_of_id,
        on_behalf_of_email=on_behalf_of_email,
        auth_method=auth_method,
        credential_id=credential_id,
        request_id=ctx.request_id if ctx is not None else None,
        source_ip=ctx.source_ip if ctx is not None else None,
        user_agent=ctx.user_agent if ctx is not None else None,
        workspace_id=getattr(workspace, "id", None),
        workspace_slug=getattr(workspace, "slug", None),
        target_type=spec.target_type,
        target_id=str(target_id) if target_id is not None else None,
        target_name=clip(target_name, 1024),
        subject_id=subject_id,
        subject_email=getattr(subject, "email", None),
        self_change=subject_id is not None and subject_id in {actor_id, on_behalf_of_id},
        before=_jsonable(before),
        after=_jsonable(after),
        details=_jsonable(details),
        error_code=error_code,
    )
    db.add(event)
    _listen(db)
    db.info.setdefault(_PENDING, []).append(event_payload(event))
    return event


def event_payload(event: AuditEvent) -> dict[str, Any]:
    """An event as the JSON the log line and the REST API carry."""
    return {
        "id": str(event.id),
        "event_time": event.event_time.isoformat(),
        "event_version": event.event_version,
        "category": event.category,
        "action": event.action,
        "outcome": event.outcome,
        "actor": {
            "id": str(event.actor_id) if event.actor_id else None,
            "email": event.actor_email,
            "type": event.actor_type,
            "on_behalf_of_id": str(event.on_behalf_of_id) if event.on_behalf_of_id else None,
            "on_behalf_of_email": event.on_behalf_of_email,
            "auth_method": event.auth_method,
            "credential_id": str(event.credential_id) if event.credential_id else None,
        },
        "request": {
            "id": event.request_id,
            "source_ip": event.source_ip,
            "user_agent": event.user_agent,
        },
        "workspace": {
            "id": str(event.workspace_id) if event.workspace_id else None,
            "slug": event.workspace_slug,
        },
        "target": {"type": event.target_type, "id": event.target_id, "name": event.target_name},
        "subject": {
            "id": str(event.subject_id) if event.subject_id else None,
            "email": event.subject_email,
        },
        "self_change": event.self_change,
        "before": event.before,
        "after": event.after,
        "details": event.details,
        "error_code": event.error_code,
    }


def _deduped(key: tuple[str, ...]) -> bool:
    """Whether ``key`` was recorded within the window; if not, mark it now."""
    now = time.monotonic()
    if len(_recent) >= _DEDUPE_MAX:
        for stale in [k for k, at in _recent.items() if now - at >= _DEDUPE_WINDOW_S]:
            del _recent[stale]
        if len(_recent) >= _DEDUPE_MAX:
            _recent.clear()
    seen = _recent.get(key)
    if seen is not None and now - seen < _DEDUPE_WINDOW_S:
        return True
    _recent[key] = now
    return False


def reset_dedupe() -> None:
    """Forget recent events; for tests, which share one process."""
    _recent.clear()


async def record_now(source: AsyncSession | Any, action: str, **fields: Any) -> None:
    """Write one event in its own transaction, never raising.

    ``source`` is the request's session or the engine it uses: the event goes to
    the same database but commits on its own, so it survives the request's
    rollback. A failure to write is logged and swallowed, since a request that
    is already failing must not fail differently because of its audit record.
    """
    ctx = current()
    actor = fields.get("actor")
    if actor is not None:
        who = str(actor.id)
    elif ctx is not None and ctx.actor_id is not None:
        who = str(ctx.actor_id)
    else:
        who = (ctx.source_ip if ctx is not None else None) or ""
    key = (who, action, str(fields.get("target_id") or ""), str(fields.get("target_name") or ""))
    if _deduped(key):
        return
    bind = source.bind if isinstance(source, AsyncSession) else source
    if bind is None and ctx is not None:
        bind = ctx.bind
    if bind is None:
        logger.warning("No database to record audit event %s in", action)
        return
    try:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            record(db, action, **fields)
            await db.commit()
    except Exception:  # noqa: BLE001 - the audit write must not change the response
        logger.exception("Failed to record audit event %s", action)
