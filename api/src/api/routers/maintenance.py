"""Lakehouse health + recommendations API (member-scoped, read-mostly).

Surfaces what the scanner has computed: explainable health scores rolled up from
table to deployment, and the recommendation feed. The only mutation is dismissing
a recommendation — V1 never applies maintenance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.maintenance import (
    DeploymentHealthOut,
    HealthHistoryPoint,
    NamespaceHealthOut,
    RecommendationOut,
    TableHealthDetailOut,
    TableHealthOut,
    WorkspaceHealthDetailOut,
    WorkspaceHealthOut,
)
from api.services.maintenance import scoring
from api.services.maintenance.read import latest_samples, sample_for_aggregate
from api.services.workspace import assert_workspace_member, get_workspace

router = APIRouter()

# Trend window returned with a table's health detail.
_HISTORY_LIMIT = 90


async def _member_workspaces(db: AsyncSession, user: User) -> list[Workspace]:
    return list(
        (
            await db.execute(
                sa.select(Workspace)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(WorkspaceMember.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )


def _to_table_out(s: TableHealthSample) -> TableHealthOut:
    return TableHealthOut(
        schema_name=s.schema_name,
        table_name=s.table_name,
        score=s.score,
        band=scoring.band(s.score),
        scanned_at=s.scanned_at,
        snapshot_count=s.snapshot_count,
        data_file_count=s.data_file_count,
        manifest_count=s.manifest_count,
        total_data_bytes=s.total_data_bytes,
        avg_file_bytes=s.avg_file_bytes,
        small_file_ratio=s.small_file_ratio,
        orphan_bytes=s.orphan_bytes,
        factors=s.factors,
    )


@router.get("/maintenance/health", response_model=DeploymentHealthOut)
async def deployment_health(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeploymentHealthOut:
    """Deployment-wide rollup across the workspaces the caller belongs to."""
    workspaces = await _member_workspaces(db, user)
    samples = await latest_samples(db, [w.id for w in workspaces])
    by_ws: dict[uuid.UUID, list[TableHealthSample]] = {}
    for s in samples:
        by_ws.setdefault(s.workspace_id, []).append(s)

    ws_out = [
        WorkspaceHealthOut(
            workspace_id=w.id,
            slug=w.slug,
            summary=scoring.aggregate([sample_for_aggregate(s) for s in by_ws.get(w.id, [])]),
        )
        for w in workspaces
    ]
    overall = scoring.aggregate([sample_for_aggregate(s) for s in samples])
    return DeploymentHealthOut(summary=overall, workspaces=ws_out)


@router.get("/workspaces/{ws}/health", response_model=WorkspaceHealthDetailOut)
async def workspace_health(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceHealthDetailOut:
    """Table health across the workspace, grouped by namespace.

    Reports the small-file and snapshot-bloat signals the maintenance scanner
    samples, so a workspace can be triaged without opening each table."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)

    samples = await latest_samples(db, [workspace.id])
    by_schema: dict[str, list[TableHealthSample]] = {}
    for s in samples:
        by_schema.setdefault(s.schema_name, []).append(s)

    namespaces = [
        NamespaceHealthOut(
            schema_name=name,
            summary=scoring.aggregate([sample_for_aggregate(s) for s in rows]),
        )
        for name, rows in sorted(by_schema.items())
    ]
    tables = sorted(
        (_to_table_out(s) for s in samples),
        key=lambda t: (t.score if t.score is not None else 999, t.schema_name, t.table_name),
    )
    summary = scoring.aggregate([sample_for_aggregate(s) for s in samples])
    return WorkspaceHealthDetailOut(summary=summary, namespaces=namespaces, tables=tables)


@router.get(
    "/workspaces/{ws}/schemas/{schema}/tables/{table}/health",
    response_model=TableHealthDetailOut,
)
async def table_health(
    ws: str,
    schema: str,
    table: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TableHealthDetailOut:
    """One table's health, with the history behind it.

    Resolves against the workspace's default catalog. The history is what makes
    the numbers actionable: a file count that is climbing needs compaction, one
    that is flat does not."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)

    history_rows = (
        (
            await db.execute(
                sa.select(TableHealthSample)
                .where(
                    TableHealthSample.workspace_id == workspace.id,
                    TableHealthSample.schema_name == schema,
                    TableHealthSample.table_name == table,
                )
                .order_by(TableHealthSample.scanned_at.desc())
                .limit(_HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if not history_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No health data yet")

    latest = history_rows[0]
    history = [
        HealthHistoryPoint(
            scanned_at=r.scanned_at, score=r.score, total_data_bytes=r.total_data_bytes
        )
        for r in reversed(history_rows)
    ]
    recs = (
        (
            await db.execute(
                sa.select(MaintenanceRecommendation)
                .where(
                    MaintenanceRecommendation.workspace_id == workspace.id,
                    MaintenanceRecommendation.schema_name == schema,
                    MaintenanceRecommendation.table_name == table,
                    MaintenanceRecommendation.status == "open",
                )
                .order_by(MaintenanceRecommendation.severity)
            )
        )
        .scalars()
        .all()
    )
    return TableHealthDetailOut(
        table=_to_table_out(latest),
        history=history,
        recommendations=[RecommendationOut.model_validate(r, from_attributes=True) for r in recs],
    )


@router.get("/maintenance/recommendations", response_model=list[RecommendationOut])
async def list_recommendations(
    status_filter: str = Query("open", alias="status"),
    severity: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RecommendationOut]:
    """Maintenance work the scanner suggests, across every workspace the caller
    is a member of.

    Defaults to `status=open`. Each recommendation names the table, why it was
    raised, and how severe it is."""
    workspaces = await _member_workspaces(db, user)
    ws_ids = [w.id for w in workspaces]
    if not ws_ids:
        return []
    stmt = sa.select(MaintenanceRecommendation).where(
        MaintenanceRecommendation.workspace_id.in_(ws_ids)
    )
    if status_filter != "all":
        stmt = stmt.where(MaintenanceRecommendation.status == status_filter)
    if severity:
        stmt = stmt.where(MaintenanceRecommendation.severity == severity)
    stmt = stmt.order_by(
        MaintenanceRecommendation.severity, MaintenanceRecommendation.created_at.desc()
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [RecommendationOut.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/maintenance/recommendations/{rec_id}/dismiss",
    response_model=RecommendationOut,
)
async def dismiss_recommendation(
    rec_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationOut:
    """Dismiss a recommendation, so the scanner stops surfacing it. Requires
    `writer` on the workspace it belongs to.

    A judgement, not a fix: the underlying condition is unchanged and the
    recommendation is kept as a dismissed record rather than deleted."""
    rec = await db.get(MaintenanceRecommendation, rec_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, rec.workspace_id, user.id, min_role="writer")
    rec.status = "dismissed"
    rec.resolved_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(rec)
    return RecommendationOut.model_validate(rec, from_attributes=True)
