"""Data paths for a DuckLake catalog migration

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-21

A DuckLake catalog has no shadow catalog to migrate through; it needs its
source and target data paths instead. No CHECK pairing them with the shadow
columns: both pairs are NULL for a ``pending`` row of either kind.

Downgrade refuses while any ``source_data_path`` is set: it is the only pointer
to data retained for rollback.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("catalog_migrations") as batch:
        batch.add_column(sa.Column("source_data_path", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("target_data_path", sa.String(2048), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    migrations = sa.table("catalog_migrations", sa.column("source_data_path"))
    retained = bind.execute(
        sa.select(sa.func.count())
        .select_from(migrations)
        .where(migrations.c.source_data_path.isnot(None))
    ).scalar_one()
    if retained:
        raise RuntimeError(
            f"Refusing to downgrade: {retained} migration(s) still retain their source "
            "data path. It is the only pointer to the data kept for rollback, so "
            "dropping it strands that prefix in object storage. Let the retention "
            "sweep clear them first, then downgrade."
        )

    with op.batch_alter_table("catalog_migrations") as batch:
        batch.drop_column("target_data_path")
        batch.drop_column("source_data_path")
