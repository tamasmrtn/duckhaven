"""Add queries.profile

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("queries", sa.Column("profile", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("queries", "profile")
