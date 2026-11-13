"""Metric presentation

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-13

How a metric's number is shown: ``format`` (number, percent, currency, duration,
bytes), a ``unit`` for plain numbers, an ISO 4217 ``currency`` for currency
metrics, and ``polarity``, which direction of change is good news. All nullable:
existing metrics come through as plain, neutral numbers. The CHECKs pin the two
vocabularies; which combinations are allowed is checked at the API edge.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("semantic_metrics") as batch:
        batch.add_column(sa.Column("format", sa.String(16), nullable=True))
        batch.add_column(sa.Column("unit", sa.String(32), nullable=True))
        batch.add_column(sa.Column("currency", sa.String(3), nullable=True))
        batch.add_column(sa.Column("polarity", sa.String(16), nullable=True))
        batch.create_check_constraint(
            "ck_semantic_metrics_format",
            "format IN ('number', 'percent', 'currency', 'duration', 'bytes')",
        )
        batch.create_check_constraint(
            "ck_semantic_metrics_polarity",
            "polarity IN ('up_good', 'down_good', 'neutral')",
        )


def downgrade() -> None:
    with op.batch_alter_table("semantic_metrics") as batch:
        batch.drop_constraint("ck_semantic_metrics_polarity", type_="check")
        batch.drop_constraint("ck_semantic_metrics_format", type_="check")
        batch.drop_column("polarity")
        batch.drop_column("currency")
        batch.drop_column("unit")
        batch.drop_column("format")
