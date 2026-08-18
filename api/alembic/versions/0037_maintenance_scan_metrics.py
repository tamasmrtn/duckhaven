"""Add snapshot-id and oldest-snapshot-age to maintenance health samples

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-18

Two nullable columns on ``table_health_sample``.

``snapshot_id`` records the table's latest Iceberg snapshot id at probe time. It
is what the scanner compares cycle over cycle to skip tables whose snapshot has
not changed (change-based incremental scanning) instead of re-probing on a fixed
cadence. ``oldest_snapshot_age_days`` records how old the oldest retained
snapshot is, the honest signal for snapshot expiration — expiration is
age-based, so scoring on the raw snapshot count was wrong (a table with 100
snapshots all created today scores poorly though age-based expiration would not
help it).

Both are nullable because a probe can degrade a metric to null without failing
the scan; existing rows simply have nulls.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("table_health_sample", sa.Column("snapshot_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "table_health_sample", sa.Column("oldest_snapshot_age_days", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("table_health_sample", "oldest_snapshot_age_days")
    op.drop_column("table_health_sample", "snapshot_id")
