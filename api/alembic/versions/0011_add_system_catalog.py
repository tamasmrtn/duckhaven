"""Add the built-in system catalog flag (schema only)

Adds ``catalogs.is_system`` and relaxes ``catalogs.created_by`` /
``storage_backends.created_by`` to nullable, since the system catalog is
DuckHaven-owned and has no human creator. The system catalog's rows + Polaris
provisioning are created at first-admin setup (on the admin-chosen storage
backend) and self-healed on startup — not in this migration — so it never has
to know about Polaris or pick a ``created_by`` user.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("catalogs") as b:
        b.add_column(
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.alter_column("created_by", existing_type=UUID(), nullable=True)
    with op.batch_alter_table("storage_backends") as b:
        b.alter_column("created_by", existing_type=UUID(), nullable=True)


def downgrade() -> None:
    # Remove any system catalog rows first so the FKs/NOT NULL can be restored.
    bind = op.get_bind()
    catalogs = sa.table("catalogs", sa.column("id"), sa.column("is_system"))
    wc = sa.table("workspace_catalogs", sa.column("catalog_id"))
    system_ids = [
        r[0] for r in bind.execute(sa.select(catalogs.c.id).where(catalogs.c.is_system)).fetchall()
    ]
    if system_ids:
        bind.execute(sa.delete(wc).where(wc.c.catalog_id.in_(system_ids)))
        bind.execute(sa.delete(catalogs).where(catalogs.c.id.in_(system_ids)))

    with op.batch_alter_table("storage_backends") as b:
        b.alter_column("created_by", existing_type=UUID(), nullable=False)
    with op.batch_alter_table("catalogs") as b:
        b.alter_column("created_by", existing_type=UUID(), nullable=False)
        b.drop_column("is_system")
