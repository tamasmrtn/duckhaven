"""Query probe

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-08

``queries.probe`` records the table probes dispatched with a run: for each kind
(``stats``, ``health``), the catalog id and the catalog, schema and table it names.
A probe result the agent reports is recorded only when it names that table.
Nullable: existing rows, and runs without a probe, carry none.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("queries") as batch:
        batch.add_column(sa.Column("probe", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("queries") as batch:
        batch.drop_column("probe")
