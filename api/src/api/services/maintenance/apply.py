"""Running the maintenance a recommendation asks for.

Gated on the kind's `supports_maintenance_apply` capability.

The statement is the Query row's SQL, not a payload key, so the `queries` audit
trail shows what actually ran and `dispatch_query`'s write-freeze applies.
`maintain_for` only asks the agent to measure the table before and after on the
same connection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.models.maintenance import MaintenanceRecommendation
from api.models.query import Query
from api.models.user import User
from api.models.workspace import Workspace
from api.services.catalog_backends import capabilities_for
from api.services.maintenance import verbs
from api.services.maintenance.policy import get_or_create_policy

logger = logging.getLogger(__name__)

# Generous because compaction is unbounded; after this the scan cycle fails the apply.
_STALE_AFTER_S = 6 * 3600


async def start_apply(
    db: AsyncSession,
    *,
    recommendation: MaintenanceRecommendation,
    catalog: Catalog,
    workspace: Workspace,
    user: User,
) -> Query:
    """Dispatch the maintenance this recommendation asks for."""
    from api.services import query as query_service
    from api.services.migration.service import active_migration

    kind = recommendation.kind
    if not capabilities_for(catalog.kind).supports_maintenance_apply:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"DuckHaven cannot run maintenance for {catalog.kind} catalogs. "
                "Use the command on the recommendation with an external engine."
            ),
        )
    if kind not in verbs.APPLICABLE_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"'{kind}' describes what to look at, not something to run.",
        )
    if recommendation.status != "open":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Recommendation is {recommendation.status}, not open.",
        )
    # Checked here so the caller gets a 409, not dispatch_query's ValueError (500).
    if await active_migration(db, catalog.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This catalog is migrating to another storage backend.",
        )
    if await _apply_running_on(db, catalog.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Maintenance is already running on this catalog.",
        )

    policy = await get_or_create_policy(db)
    retention = int(policy.thresholds.get("snapshot_retention_days", 7))
    statements = verbs.render(
        kind,
        catalog=catalog.slug,
        schema=recommendation.schema_name,
        table=recommendation.table_name,
        retention_days=retention,
    )

    agent = await query_service.pick_agent_for(db, workspace, principal_id=user.id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No compatible agent is connected to run maintenance.",
        )

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=user.id,
        sql=";\n".join(statements),
        status="queued",
        # Unlike the read-only "maintenance" probe, visible in the query log.
        origin="maintenance_apply",
    )
    db.add(query)
    await db.flush()

    recommendation.apply_status = "running"
    recommendation.apply_error = None
    recommendation.applied_at = datetime.now(tz=UTC)
    recommendation.applied_by = user.id
    recommendation.applied_query_id = query.id
    await db.commit()

    await query_service.dispatch_query(
        db,
        query,
        timeout_s=float(_STALE_AFTER_S),
        active_catalog=catalog.slug,
        principal_id=user.id,
        maintain_for={
            "catalog": catalog.slug,
            "schema": recommendation.schema_name,
            "table": recommendation.table_name,
        },
    )
    return query


async def _apply_running_on(db: AsyncSession, catalog_id: uuid.UUID) -> bool:
    """Whether any recommendation on this catalog has an apply in flight.

    Per catalog: `expire_snapshots` and `cleanup_orphans` act on the whole catalog.
    """
    found = await db.scalar(
        select(MaintenanceRecommendation.id).where(
            MaintenanceRecommendation.catalog_id == catalog_id,
            MaintenanceRecommendation.apply_status == "running",
        )
    )
    return found is not None


async def record_apply_result(db: AsyncSession, query: Query, payload: dict[str, Any]) -> None:
    """Settle the recommendation this query was applying.

    Success does not resolve the recommendation; the next health sample decides.
    """
    recommendation = (
        await db.execute(
            select(MaintenanceRecommendation).where(
                MaintenanceRecommendation.applied_query_id == query.id
            )
        )
    ).scalar_one_or_none()
    if recommendation is None:
        return

    if query.status == "done":
        recommendation.apply_status = "succeeded"
        recommendation.apply_result = payload.get("maintenance")
    else:
        recommendation.apply_status = "failed"
        recommendation.apply_error = (query.error or query.status)[:1000]
    await db.commit()


async def sweep_stale_applies(db: AsyncSession, now: datetime) -> int:
    """Fail applies whose query never came back, so they stop blocking the catalog."""
    cutoff = now.timestamp() - _STALE_AFTER_S
    stale = (
        (
            await db.execute(
                select(MaintenanceRecommendation).where(
                    MaintenanceRecommendation.apply_status == "running"
                )
            )
        )
        .scalars()
        .all()
    )
    swept = 0
    for rec in stale:
        applied_at = rec.applied_at
        if applied_at is None:
            continue
        if applied_at.tzinfo is None:
            applied_at = applied_at.replace(tzinfo=UTC)
        if applied_at.timestamp() > cutoff:
            continue
        rec.apply_status = "failed"
        rec.apply_error = "The agent never reported back; the apply was abandoned."
        swept += 1
    if swept:
        await db.commit()
    return swept
