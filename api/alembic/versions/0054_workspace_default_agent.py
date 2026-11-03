"""Workspace default compute

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-03

``workspaces.default_agent_id`` names the agent that runs work when a caller names
none: the assistant, MCP, describe, SQL sessions. It is nullable and additive, so an
existing workspace keeps today's behaviour (the server picks a connected agent, or
starts one when elastic compute is on). The agent being deleted clears the pointer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("default_agent_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_workspaces_default_agent",
            "agents",
            ["default_agent_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_constraint("fk_workspaces_default_agent", type_="foreignkey")
        batch.drop_column("default_agent_id")
