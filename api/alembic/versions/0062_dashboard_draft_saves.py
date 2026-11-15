"""Dashboard draft saves

Revision ID: 0062
Revises: 0061
Create Date: 2026-11-15

``dashboard_draft_saves`` records each dashboard draft an MCP client saves, so
the server can limit how many one person saves an hour. Rows older than an
hour are deleted when the same person next saves one.

The table is new, so the migration changes no existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _parent(name: str, table: str) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "dashboard_draft_saves",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _parent("workspace_id", "workspaces"),
        _parent("user_id", "users"),
        _parent("dashboard_id", "dashboards"),
        sa.Column(
            "saved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_dashboard_draft_saves_user",
        "dashboard_draft_saves",
        ["workspace_id", "user_id", "saved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dashboard_draft_saves_user", table_name="dashboard_draft_saves")
    op.drop_table("dashboard_draft_saves")
