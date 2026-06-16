"""Read helpers for the maintenance API: latest-sample resolution and rollups."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.maintenance import TableHealthSample


async def latest_samples(
    db: AsyncSession, workspace_ids: Sequence[uuid.UUID]
) -> list[TableHealthSample]:
    """The most recent health sample per (workspace, schema, table).

    Uses a max(scanned_at) group-by join rather than ``DISTINCT ON`` so it runs
    on both Postgres and the SQLite test database.
    """
    if not workspace_ids:
        return []
    latest = (
        sa.select(
            TableHealthSample.workspace_id.label("wid"),
            TableHealthSample.schema_name.label("sn"),
            TableHealthSample.table_name.label("tn"),
            sa.func.max(TableHealthSample.scanned_at).label("mx"),
        )
        .where(TableHealthSample.workspace_id.in_(workspace_ids))
        .group_by(
            TableHealthSample.workspace_id,
            TableHealthSample.schema_name,
            TableHealthSample.table_name,
        )
        .subquery()
    )
    rows = (
        await db.execute(
            sa.select(TableHealthSample).join(
                latest,
                sa.and_(
                    TableHealthSample.workspace_id == latest.c.wid,
                    TableHealthSample.schema_name == latest.c.sn,
                    TableHealthSample.table_name == latest.c.tn,
                    TableHealthSample.scanned_at == latest.c.mx,
                ),
            )
        )
    ).scalars()
    return list(rows)


def sample_for_aggregate(sample: TableHealthSample) -> dict:
    return {"score": sample.score, "total_data_bytes": sample.total_data_bytes}
