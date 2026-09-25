"""Default saved_queries.updated_at to now()

Revision ID: 0048
Revises: 0047
Create Date: 2026-09-25

0047 made ``updated_at`` NOT NULL without the ``now()`` default the model
declares, so an insert that leaves it to the database failed with a null
violation. Unit tests build their schema from the models and never saw it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("saved_queries") as batch:
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.func.now(),
        )


def downgrade() -> None:
    with op.batch_alter_table("saved_queries") as batch:
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
