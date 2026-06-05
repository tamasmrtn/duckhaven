"""Add Iceberg-native metadata to table_metadata

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("table_metadata", sa.Column("snapshot_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "table_metadata", sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("table_metadata", sa.Column("data_file_count", sa.Integer(), nullable=True))
    op.add_column("table_metadata", sa.Column("has_deletes", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("table_metadata", "has_deletes")
    op.drop_column("table_metadata", "data_file_count")
    op.drop_column("table_metadata", "snapshot_at")
    op.drop_column("table_metadata", "snapshot_id")
