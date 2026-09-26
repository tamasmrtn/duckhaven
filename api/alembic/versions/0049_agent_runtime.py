"""Agent runtimes: the runtime an elastic agent runs, and which one ran each piece of work

Revision ID: 0049
Revises: 0048
Create Date: 2026-09-26

``agents.requested_runtime_id`` is the runtime (duckhaven_shared.runtimes) an
elastic agent is provisioned and restarted as — authoritative for elastic agents,
the way ``requested_cpu`` is. A static agent's runtime is whatever its image is,
and it reports that in ``capabilities``, so the column stays NULL for it.

``queries.runtime_id`` / ``sql_sessions.runtime_id`` record which runtime ran the
work. ``agent_id`` cannot say that later: a static agent can be re-imaged.

Every elastic agent before this revision ran the only image there was, the 1.5
line. The literal is frozen here rather than read from the manifest, which will
move on.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("requested_runtime_id", sa.String(32), nullable=True))
    op.add_column("queries", sa.Column("runtime_id", sa.String(32), nullable=True))
    op.add_column("sql_sessions", sa.Column("runtime_id", sa.String(32), nullable=True))
    op.execute("UPDATE agents SET requested_runtime_id = '1.5' WHERE provider IS NOT NULL")


def downgrade() -> None:
    op.drop_column("sql_sessions", "runtime_id")
    op.drop_column("queries", "runtime_id")
    op.drop_column("agents", "requested_runtime_id")
