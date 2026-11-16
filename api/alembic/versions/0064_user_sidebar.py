"""User sidebar

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-16

``users.sidebar`` holds how the user wants the main navigation laid out --
``expanded``, ``collapsed`` or ``hover`` -- so the choice follows them to every
browser they sign in from.

The column is nullable and left empty on existing rows. Empty means the user
never chose, so they get whatever the app's default is, including a default
changed later.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sidebar", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "sidebar")
