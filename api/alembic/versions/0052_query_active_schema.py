"""Query active schema

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-14

``queries.active_schema`` records the schema a run resolved unqualified table
names in, beside the catalog already in ``active_catalog``. Reading a result
re-checks the reader's grants on the SQL that produced it, and a session can
``USE`` another schema, so both halves of that context are kept on the row.
Nullable: an existing row, and every one-shot run, resolved in the workspace
default schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("queries") as batch:
        batch.add_column(sa.Column("active_schema", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("queries") as batch:
        batch.drop_column("active_schema")
