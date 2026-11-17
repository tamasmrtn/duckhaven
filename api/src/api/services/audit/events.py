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
from api.services.audit.record import record


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
