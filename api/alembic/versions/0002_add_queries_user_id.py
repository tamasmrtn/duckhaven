"""Add user_id to queries (audit)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "queries",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_queries_user_id_users",
        source_table="queries",
        referent_table="users",
        local_cols=["user_id"],
        remote_cols=["id"],
    )
    op.create_index("ix_queries_user_id", "queries", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_queries_user_id", "queries")
    op.drop_constraint("fk_queries_user_id_users", "queries", type_="foreignkey")
    op.drop_column("queries", "user_id")
