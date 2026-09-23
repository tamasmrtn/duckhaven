"""Record of a maintenance operation DuckHaven ran

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-21

Each recommendation gains the outcome of the last apply against it. ``status``
gains no ``applied`` value: only the next scan can tell whether the finding
cleared.

Downgrade refuses once any apply has happened: these columns are the record
that data files were rewritten or deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("maintenance_recommendation") as batch:
        batch.add_column(sa.Column("apply_status", sa.String(20), nullable=True))
        batch.add_column(sa.Column("apply_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("apply_result", _JSON, nullable=True))
        batch.add_column(sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("applied_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("applied_query_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_maintenance_recommendation_applied_by", "users", ["applied_by"], ["id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    recs = sa.table("maintenance_recommendation", sa.column("apply_status"))
    applied = bind.execute(
        sa.select(sa.func.count()).select_from(recs).where(recs.c.apply_status.isnot(None))
    ).scalar_one()
    if applied:
        raise RuntimeError(
            f"Refusing to downgrade: {applied} recommendation(s) record a maintenance "
            "apply. Together with the query they point at, these columns are the only "
            "record that DuckHaven rewrote or deleted data files. Clear them "
            "deliberately first if you really mean to lose that."
        )

    with op.batch_alter_table("maintenance_recommendation") as batch:
        batch.drop_constraint("fk_maintenance_recommendation_applied_by", type_="foreignkey")
        for column in (
            "applied_query_id",
            "applied_by",
            "applied_at",
            "apply_result",
            "apply_error",
            "apply_status",
        ):
            batch.drop_column(column)
