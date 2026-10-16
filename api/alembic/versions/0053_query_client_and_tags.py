"""Query client and tags

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-15

``queries`` gains who sent each run and what it was for: ``client_name`` and
``client_version`` from the caller's ``User-Agent`` (a session statement takes
its session's), and ``tags``, free-form key/value labels the caller attaches.
All nullable: existing rows, and runs the platform starts itself, carry none.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("queries") as batch:
        batch.add_column(sa.Column("client_name", sa.String(64), nullable=True))
        batch.add_column(sa.Column("client_version", sa.String(32), nullable=True))
        batch.add_column(sa.Column("tags", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("queries") as batch:
        batch.drop_column("tags")
        batch.drop_column("client_version")
        batch.drop_column("client_name")
