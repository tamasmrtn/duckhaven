"""Dashboard previews

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-14

``dashboard_previews`` holds the dashboard files writers are trying out before
saving them: one row per preview, readable and runnable by its creator only. A
preview expires two hours after it last changed; expired rows are deleted when
the next preview is created. The rows live in the database rather than in one
replica's memory, so any replica can serve a preview's page.

The table is new, so the migration changes no existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def _parent(name: str, table: str) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=False
    )


def _time(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "dashboard_previews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _parent("workspace_id", "workspaces"),
        _parent("created_by", "users"),
        sa.Column("spec", _JSON, nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        _time("created_at"),
        _time("updated_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dashboard_previews_expires_at", "dashboard_previews", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_previews_expires_at", table_name="dashboard_previews")
    op.drop_table("dashboard_previews")
