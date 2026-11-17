"""Audit events that several routes record the same way.

Each helper builds the event from the objects at hand, so every place that
grants or removes access describes it identically: the same target name, the
same before/after fields, the same subject.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.models.user import User
from api.models.workspace import Workspace
from api.services.audit.context import current, current_route
from api.services.audit.record import clip, record, record_now


def grant_name(catalog_slug: str, schema: str | None, table: str | None) -> str:
    """The object a data grant covers, as SQL names it: ``sales.analytics.orders``."""
    return ".".join(part for part in (catalog_slug, schema, table) if part)


async def users_by_id(db: AsyncSession, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, User]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await db.scalars(select(User).where(User.id.in_(wanted)))
    return {u.id: u for u in rows}


def _tier(value: str | None) -> dict[str, str] | None:
    return None if value is None else {"tier": value}


def record_catalog_created(
    db: AsyncSession,
    catalog: Catalog,
    *,
    workspace: Any,
    link: Any,
    backend: Any,
    details: dict | None = None,
    actor: Any = None,
) -> None:
    """A new catalog, and the attachment it was created with."""
    record(
        db,
        "catalog.create",
        target_id=catalog.id,
        target_name=catalog.slug,
        workspace=workspace,
        details={
            "kind": catalog.kind,
            "storage_backend_id": str(backend.id),
            "storage_kind": backend.kind,
            **(details or {}),
        },
        actor=actor,
    )
    record_catalog_attached(
        db, catalog, workspace=workspace, link=link, details=details, actor=actor
    )


def record_catalog_attached(
    db: AsyncSession,
    catalog: Catalog,
    *,
    workspace: Any,
    link: Any,
    details: dict | None = None,
    actor: Any = None,
) -> None:
    """A catalog newly attached to a workspace, in the mode it starts in."""
    record(
        db,
        "catalog.attach",
        target_id=catalog.id,
        target_name=catalog.slug,
        workspace=workspace,
        after={"access_mode": link.access_mode},
        details={"default": link.is_default, **(details or {})},
        actor=actor,
    )


def record_membership(
    db: AsyncSession,
    action: str,
    *,
    workspace: Any,
    member: User,
    before: str | None = None,
    after: str | None = None,
    details: dict | None = None,
) -> None:
    """A workspace membership added, re-roled or removed; ``before``/``after`` are roles."""
    record(
        db,
        action,
        target_id=member.id,
        target_name=member.email,
        workspace=workspace,
        subject=member,
        before=None if before is None else {"role": before},
        after=None if after is None else {"role": after},
        details=details,
    )


def record_account_update(
    db: AsyncSession,
    prefix: str,
    account: User,
    *,
    role: str | None,
    is_active: bool | None,
) -> None:
    """An account's global role or active state changed; call before applying it.

    ``prefix`` is ``user`` or ``service_account``. A value that is not changing
    records nothing.
    """
    if role is not None and role != account.role:
        record(
            db,
            f"{prefix}.role_change",
            target_id=account.id,
            target_name=account.email,
            subject=account,
            before={"role": account.role},
            after={"role": role},
        )
    if is_active is not None and is_active != account.is_active:
        record(
            db,
            f"{prefix}.{'reactivate' if is_active else 'deactivate'}",
            target_id=account.id,
            target_name=account.email,
            subject=account,
            before={"is_active": account.is_active},
            after={"is_active": is_active},
        )


def record_sign_in(db: AsyncSession, user: User, *, method: str, **details: Any) -> None:
    """A session started; call before the commit that creates it."""
    record(
        db,
        "auth.sign_in",
        target_id=user.id,
        target_name=user.email,
        details={"method": method, **details},
        actor=user,
    )


async def record_sign_in_failed(
    db: AsyncSession, identifier: str | None, *, method: str, **details: Any
) -> None:
    """A sign-in refused; written on its own, since the request fails.

    ``identifier`` is what was submitted, cut short, and never checked against
    the accounts: whether it names one is not something to give away.
    """
    await record_now(
        db,
        "auth.sign_in_failed",
        target_name=clip(identifier, 255),
        outcome="failure",
        details={"method": method, **details},
    )


async def record_rate_limited(db: AsyncSession, identifier: str | None, *, method: str) -> None:
    await record_now(
        db,
        "auth.rate_limited",
        target_name=clip(identifier, 255),
        outcome="denied",
        error_code="too_many_sign_in_attempts",
        details={"method": method},
    )


async def record_grant_denied(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    principal_id: uuid.UUID | None,
    reason: str,
    sql: str | None,
) -> None:
    """A statement refused by a data grant check; written on its own, as the request fails.

    In a request the caller is the actor. A run outside one (a schedule firing)
    is recorded as its principal's, the account the check was made for.
    """
    ctx = current()
    actor = None
    if (ctx is None or ctx.actor_id is None) and principal_id is not None:
        actor = await db.get(User, principal_id)
    await record_now(
        db,
        "authz.grant_denied",
        target_name=current_route(),
        workspace=await db.get(Workspace, workspace_id),
        outcome="denied",
        error_code="grant_denied",
        details={"reason": clip(reason, 512), "statement": clip(sql)},
        actor=actor,
    )


async def record_permission_denied(db: AsyncSession, permission: str) -> None:
    """A request refused for lacking a global permission."""
    await record_now(
        db,
        "authz.permission_denied",
        target_name=current_route(),
        outcome="denied",
        error_code="forbidden",
        details={"permission": permission},
    )


async def record_catalog_grant(
    db: AsyncSession,
    action: str,
    *,
    grant_id: uuid.UUID,
    user_id: uuid.UUID,
    catalog: Catalog,
    schema: str | None,
    table: str | None,
    workspace: Any = None,
    before: str | None = None,
    after: str | None = None,
    reason: str | None = None,
) -> None:
    """One data grant created, changed or removed; ``before``/``after`` are tiers."""
    subject = await db.get(User, user_id)
    record(
        db,
        action,
        target_id=grant_id,
        target_name=grant_name(catalog.slug, schema, table),
        workspace=workspace,
        subject=subject,
        before=_tier(before),
        after=_tier(after),
        details={
            "catalog_id": str(catalog.id),
            "catalog": catalog.slug,
            "schema": schema,
            "table": table,
            **({"reason": reason} if reason else {}),
        },
    )


async def record_catalog_grants_removed(
    db: AsyncSession,
    rows: Sequence[Any],
    *,
    catalog: Catalog,
    workspace: Any = None,
    reason: str,
) -> None:
    """Grants removed in bulk, one event each.

    ``rows`` carry ``id``, ``user_id``, ``schema_name``, ``table_name`` and
    ``tier``, as a ``DELETE ... RETURNING`` of them does.
    """
    subjects = await users_by_id(db, (row.user_id for row in rows))
    for row in rows:
        record(
            db,
            "catalog_grant.delete",
            target_id=row.id,
            target_name=grant_name(catalog.slug, row.schema_name, row.table_name),
            workspace=workspace,
            subject=subjects.get(row.user_id),
            before=_tier(row.tier),
            details={
                "catalog_id": str(catalog.id),
                "catalog": catalog.slug,
                "schema": row.schema_name,
                "table": row.table_name,
                "reason": reason,
            },
        )
