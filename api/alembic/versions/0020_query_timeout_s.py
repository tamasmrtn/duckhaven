"""Query timeout_s

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-17

Adds ``queries.timeout_s`` — the client-requested wall-clock budget for a run.

Previously the timeout travelled only on the wire (the EXEC_STATEMENT payload) and
was enforced solely inside the agent, around execution. Nothing server-side could
bound a statement that never started, so a lost frame left the row ``queued``
forever (#156). Persisting the budget lets the SQL-session reaper fail statements
that outlive it.

Nullable: rows written before this migration have no recorded budget, and the
reaper falls back to the configured default for them. Additive — no data
migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("queries", sa.Column("timeout_s", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("queries", "timeout_s")
