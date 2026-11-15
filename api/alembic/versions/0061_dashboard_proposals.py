"""Dashboard proposals

Revision ID: 0061
Revises: 0060
Create Date: 2026-11-15

``dashboard_proposals`` holds the changes the assistant proposes to a
dashboard: the operations, the dashboard version they were made against, and
the file they make. The person the proposal was made for accepts it, which
saves that file as the dashboard's draft, or discards it. A proposal expires a
day after it was made; expired rows are deleted when the next proposal is made.

The table is new, so the migration changes no existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def _parent(name: str, table: str) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "dashboard_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _parent("workspace_id", "workspaces"),
        _parent("dashboard_id", "dashboards"),
        _parent("created_by", "users"),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("operations", _JSON, nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("base_hash", sa.String(64), nullable=False),
        sa.Column("result_spec", _JSON, nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("changes", _JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'discarded')", name="ck_dashboard_proposals_status"
        ),
    )
    op.create_index("ix_dashboard_proposals_expires_at", "dashboard_proposals", ["expires_at"])
    op.create_index("ix_dashboard_proposals_dashboard", "dashboard_proposals", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_proposals_dashboard", table_name="dashboard_proposals")
    op.drop_index("ix_dashboard_proposals_expires_at", table_name="dashboard_proposals")
    op.drop_table("dashboard_proposals")
