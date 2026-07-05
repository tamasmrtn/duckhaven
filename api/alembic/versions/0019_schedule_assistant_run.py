"""Schedule support for the assistant_run job type

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-05

Adds the nullable ``schedules.assistant_prompt`` column that carries the
natural-language prompt for a ``job_type="assistant_run"`` schedule — an
unattended assistant turn on a cron cadence. Additive/nullable; existing
``saved_query`` schedules are unaffected.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("assistant_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("schedules", "assistant_prompt")
