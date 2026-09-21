"""Data paths for a DuckLake catalog migration

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-21

Migrating an Iceberg catalog provisions a shadow Polaris catalog at the target
and re-points the row at it. A DuckLake catalog has no shadow: its file paths
are relative to a ``data_path`` recorded in the catalog itself, so relocating it
is a prefix copy plus one row update. What it needs instead is where its data
started and where it is going.

No check constraint pairing these with the shadow columns. "Exactly one of the
two pairs" reads as the right invariant and is wrong: both are NULL for a
``pending`` row of either kind. The discriminator is ``catalogs.kind`` on the
joined row, as it is for every other per-kind difference here.

Downgrade refuses while any ``source_data_path`` is set: it is the only pointer
to data retained for rollback, and dropping it silently strands that prefix in
object storage with nothing referencing it.
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
