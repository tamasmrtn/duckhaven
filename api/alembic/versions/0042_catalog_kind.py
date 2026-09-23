"""Catalog kind: iceberg_polaris or ducklake

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-16

Adds ``kind``, which decides the identity column: ``iceberg_polaris`` keeps
``polaris_name``; ``ducklake`` carries ``metadata_schema`` instead. A CHECK
constraint keeps the pair exclusive. Existing rows are backfilled to
``iceberg_polaris``.

Downgrade refuses while any DuckLake catalog exists: ``metadata_schema`` is the
only pointer to its metadata.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_CHECK = (
    "(kind = 'iceberg_polaris' AND polaris_name IS NOT NULL AND metadata_schema IS NULL) OR "
    "(kind = 'ducklake' AND metadata_schema IS NOT NULL AND polaris_name IS NULL)"
)


def _backfill(bind: sa.engine.Connection) -> None:
    """Stamp every pre-existing catalog as Iceberg + Polaris (SQLite-safe, as in 0010)."""
    catalogs = sa.table("catalogs", sa.column("kind"))
    bind.execute(catalogs.update().where(catalogs.c.kind.is_(None)).values(kind="iceberg_polaris"))


def upgrade() -> None:
    op.add_column("catalogs", sa.Column("kind", sa.String(32), nullable=True))
    op.add_column("catalogs", sa.Column("metadata_schema", sa.String(63), nullable=True))

    _backfill(op.get_bind())

    with op.batch_alter_table("catalogs") as batch:
        batch.alter_column(
            "kind",
            existing_type=sa.String(32),
            nullable=False,
            server_default="iceberg_polaris",
        )
        batch.alter_column("polaris_name", existing_type=sa.String(255), nullable=True)
        batch.create_unique_constraint("uq_catalogs_metadata_schema", ["metadata_schema"])
        batch.create_check_constraint("ck_catalogs_kind_identity", _KIND_CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    catalogs = sa.table("catalogs", sa.column("kind"))
    remaining = bind.execute(
        sa.select(sa.func.count()).select_from(catalogs).where(catalogs.c.kind == "ducklake")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"Refusing to downgrade: {remaining} DuckLake catalog(s) still exist. "
            "Their metadata_schema is the only pointer to their metadata in the "
            "`ducklake` database — dropping this column would strand it. Drop those "
            "catalogs first (this deletes their data), then downgrade."
        )

    with op.batch_alter_table("catalogs") as batch:
        batch.drop_constraint("ck_catalogs_kind_identity", type_="check")
        batch.drop_constraint("uq_catalogs_metadata_schema", type_="unique")
        batch.alter_column("polaris_name", existing_type=sa.String(255), nullable=False)
    op.drop_column("catalogs", "metadata_schema")
    op.drop_column("catalogs", "kind")
