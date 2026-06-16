"""Ingest a health probe result: persist the sample, score it, sync recommendations.

Called from the agent WebSocket frame handler when a QUERY_DONE carries a
``health`` bundle, mirroring how ``_upsert_table_stats`` persists table stats.
Keeping ingestion off the scanner's hot path means probes stream back and are
recorded as they complete, rather than the scanner blocking per table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.query import Query
from api.services.maintenance import recommend, scoring
from api.services.maintenance.policy import get_or_create_policy

# Window of prior samples used to detect abnormal storage growth.
_GROWTH_WINDOW_DAYS = 30

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

_METRIC_FIELDS = (
    "snapshot_count",
    "data_file_count",
    "manifest_count",
    "total_data_bytes",
    "avg_file_bytes",
    "metadata_bytes",
    "orphan_bytes",
    "orphan_file_count",
    "small_file_ratio",
)


async def record_health_sample(db: AsyncSession, query: Query, health: dict[str, Any]) -> None:
    schema = health.get("schema")
    table = health.get("table")
    if not schema or not table:
        return

    policy = await get_or_create_policy(db)
    thresholds = policy.thresholds
    score, factors = scoring.score_table(health, thresholds)

    sample = TableHealthSample(
        workspace_id=query.workspace_id,
        schema_name=schema,
        table_name=table,
        score=score,
        factors=factors,
        **{f: health.get(f) for f in _METRIC_FIELDS},
    )
    db.add(sample)

    history = await _growth_history(db, query.workspace_id, schema, table)
    recs = recommend.generate(health, thresholds, history=history)
    await _sync_recommendations(db, query.workspace_id, schema, table, recs)
    await db.commit()


async def _growth_history(
    db: AsyncSession, workspace_id: uuid.UUID, schema: str, table: str
) -> list[dict[str, Any]]:
    cutoff = datetime.now(tz=UTC) - timedelta(days=_GROWTH_WINDOW_DAYS)
    rows = (
        await db.execute(
            sa.select(TableHealthSample.total_data_bytes).where(
                TableHealthSample.workspace_id == workspace_id,
                TableHealthSample.schema_name == schema,
                TableHealthSample.table_name == table,
                TableHealthSample.scanned_at >= cutoff,
            )
        )
    ).all()
    return [{"total_data_bytes": r[0]} for r in rows if r[0] is not None]


async def _sync_recommendations(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    schema: str,
    table: str,
    recs: list[dict[str, Any]],
) -> None:
    """Upsert generated recommendations and resolve ones that no longer apply.

    At most one row per (table, kind). A regenerated rec reopens unless it was
    dismissed — a dismissed rec only reappears if its severity has increased
    ("worsens again"). Kinds no longer generated are auto-resolved.
    """
    existing = {
        r.kind: r
        for r in (
            await db.execute(
                sa.select(MaintenanceRecommendation).where(
                    MaintenanceRecommendation.workspace_id == workspace_id,
                    MaintenanceRecommendation.schema_name == schema,
                    MaintenanceRecommendation.table_name == table,
                )
            )
        )
        .scalars()
        .all()
    }
    now = datetime.now(tz=UTC)
    generated_kinds = set()

    for rec in recs:
        kind = rec["kind"]
        generated_kinds.add(kind)
        row = existing.get(kind)
        if row is None:
            db.add(
                MaintenanceRecommendation(
                    workspace_id=workspace_id,
                    schema_name=schema,
                    table_name=table,
                    status="open",
                    **rec,
                )
            )
            continue
        worsened = _SEVERITY_RANK[rec["severity"]] > _SEVERITY_RANK.get(row.severity, -1)
        if row.status == "dismissed" and not worsened:
            continue  # keep suppressed until it worsens
        row.severity = rec["severity"]
        row.confidence = rec["confidence"]
        row.rationale = rec["rationale"]
        row.estimated_impact = rec["estimated_impact"]
        row.remediation = rec["remediation"]
        row.status = "open"
        row.resolved_at = None

    for kind, row in existing.items():
        if kind not in generated_kinds and row.status == "open":
            row.status = "resolved"
            row.resolved_at = now
