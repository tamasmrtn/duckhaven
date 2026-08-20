"""Add description to workspaces

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-20

Nullable free-text column for the new Settings > Workspace page. Workspaces
had no description field at all until now — existing rows simply have null.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "description")
