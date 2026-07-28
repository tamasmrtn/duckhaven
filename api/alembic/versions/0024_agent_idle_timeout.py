"""Per-agent idle timeout

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-25

Adds ``agents.idle_timeout_s`` — a per-elastic-agent idle scale-in timeout chosen
at create time. NULL falls back to the global ``elastic_idle_timeout_s``. Nullable
and additive — no data migration; static agents keep it NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("idle_timeout_s", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "idle_timeout_s")
