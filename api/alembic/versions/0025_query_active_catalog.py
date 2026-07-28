"""Remember a query's active catalog

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-26

Adds ``queries.active_catalog`` — the catalog the worksheet had selected when a run
was submitted, so a run that has to be replayed later still resolves unqualified
table names the way the user meant.

Only elastic pool runs are replayed today: one parked during a cold start is
dispatched by ``compute.service.bind_queued_work`` once an agent registers, which
happens outside the original request and so cannot recover the value from it.
Without this the replay fell back to the workspace default catalog.

Nullable and additive — an existing row simply has no recorded catalog and keeps
falling back to the default, exactly as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("queries", sa.Column("active_catalog", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("queries", "active_catalog")
