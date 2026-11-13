"""Workspace time zone

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-13

``workspaces.timezone`` is the IANA zone a workspace's relative time windows
resolve in, so "month to date" on the 1st means this month wherever the server
runs. Every existing workspace keeps the UTC it has always used until an owner
sets one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"))


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("timezone")
