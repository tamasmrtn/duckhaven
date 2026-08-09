"""Elastic agent max timeout

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-07

Elastic agents (Azure Container Instances and Docker) had no way to receive a
query timeout ceiling above the agent image's 600s code default — the compute
backends' env-var lists never carried ``MAX_TIMEOUT_S``, so any query longer than
ten minutes on a provisioned agent was interrupted regardless of what the caller
requested. ``requested_max_timeout_s`` lets ``POST /admin/agents/elastic`` choose a
per-agent ceiling, persisted so a restart re-provisions with the same value —
mirroring ``requested_cpu``/``requested_memory_gb``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(sa.Column("requested_max_timeout_s", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("requested_max_timeout_s")
