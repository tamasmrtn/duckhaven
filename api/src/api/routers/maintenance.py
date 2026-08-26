"""Lakehouse health + recommendations API (member-scoped, read-mostly).

Surfaces what the scanner has computed: explainable health scores rolled up from
table to deployment, and the recommendation feed. The only mutation is dismissing
a recommendation — V1 never applies maintenance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
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
from api.schemas.page import Page
from api.services.maintenance import scoring
from api.services.maintenance.read import latest_samples, sample_for_aggregate
from api.services.maintenance.recommend import SEVERITY_RANK
from api.services.paging import paginate
from api.services.workspace import assert_workspace_member, get_workspace, resolve_catalog

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


@router.get("/workspaces/{workspace}/health", response_model=WorkspaceHealthDetailOut)
async def workspace_health(
    ws: Annotated[str, Path(alias="workspace")],
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


async def table_health(
    ws: Annotated[str, Path(alias="workspace")],
    schema: str,
    table: str,
    catalog: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TableHealthDetailOut:
    """One table's health, with the history behind it.

    The history is what makes the numbers actionable: a file count that is
    climbing needs compaction, one that is flat does not. Samples are narrowed to
    the catalog in the path, so two attached catalogs holding a table of the same
    name report separately."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)

    scope = [
        TableHealthSample.workspace_id == workspace.id,
        TableHealthSample.schema_name == schema,
        TableHealthSample.table_name == table,
    ]
    if catalog is not None:
        cat = await resolve_catalog(db, workspace.id, catalog)
        scope.append(TableHealthSample.catalog_id == cat.id)

    history_rows = (
        (
            await db.execute(
                sa.select(TableHealthSample)
                .where(*scope)
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


#: `severity` in rank order, for sorting. Shares SEVERITY_RANK with the scanner
#: so the list and the thing that produced it cannot disagree about "most
#: severe". Unknown values sort last rather than first.
_SEVERITY_RANK = sa.case(SEVERITY_RANK, value=MaintenanceRecommendation.severity, else_=99)


@router.get("/maintenance/recommendations", response_model=Page[RecommendationOut])
async def list_recommendations(
    status_filter: list[str] | None = Query(default=None, alias="status"),
    severity: list[str] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[RecommendationOut]:
    """Maintenance work the scanner suggests, across every workspace the caller
    is a member of.

    Most severe first, then newest. Each recommendation names the table, why it
    was raised, and how severe it is. Omitting `status` returns every state --
    pass `status=open` for the ones still outstanding.
    """
    workspaces = await _member_workspaces(db, user)
    ws_ids = [w.id for w in workspaces]
    if not ws_ids:
        return Page[RecommendationOut](items=[])
    stmt = sa.select(MaintenanceRecommendation).where(
        MaintenanceRecommendation.workspace_id.in_(ws_ids)
    )
    if status_filter:
        stmt = stmt.where(MaintenanceRecommendation.status.in_(status_filter))
    if severity:
        stmt = stmt.where(MaintenanceRecommendation.severity.in_(severity))
    rows, next_cursor, has_more = await paginate(
        db,
        stmt,
        # Severity leads the sort, so it leads the cursor too: a page cut on
        # time alone would reorder rows the moment a more severe one landed.
        # Ranked, not compared as a string -- `critical` sorts after `info`
        # alphabetically, which would bury exactly the rows this list exists to
        # surface.
        sort=[
            _SEVERITY_RANK.asc(),
            MaintenanceRecommendation.created_at.desc(),
            MaintenanceRecommendation.id.desc(),
        ],
        limit=limit,
        cursor=cursor,
    )
    return Page[RecommendationOut](
        items=[RecommendationOut.model_validate(r[0], from_attributes=True) for r in rows],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/maintenance/recommendations/{recommendation_id}/dismiss",
    response_model=RecommendationOut,
)
async def dismiss_recommendation(
    recommendation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationOut:
    """Dismiss a recommendation, so the scanner stops surfacing it. Requires
    `writer` on the workspace it belongs to.

    A judgement, not a fix: the underlying condition is unchanged and the
    recommendation is kept as a dismissed record rather than deleted."""
    rec = await db.get(MaintenanceRecommendation, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, rec.workspace_id, user.id, min_role="writer")
    rec.status = "dismissed"
    rec.resolved_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(rec)
    return RecommendationOut.model_validate(rec, from_attributes=True)


router.add_api_route(
    "/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}/health",
    table_health,
    methods=["GET"],
    response_model=TableHealthDetailOut,
)
