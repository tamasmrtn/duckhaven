"""External storage credential config on storage_backends

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-26

Adds a nullable ``config`` JSONB column holding kind-specific credential
config for external backends (S3 role ARN / external id / region; ADLS tenant
id / app / consent) and drops the vestigial ``uc_storage_credential_id`` column,
which was defined but never read or written.

Backward-compat: existing ``object_store`` rows keep ``config = NULL`` and are
unaffected. ``uc_storage_credential_id`` carried no data, so dropping it is safe.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "storage_backends",
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_column("storage_backends", "uc_storage_credential_id")


def downgrade() -> None:
    op.add_column(
        "storage_backends",
        sa.Column("uc_storage_credential_id", sa.String(255), nullable=True),
    )
    op.drop_column("storage_backends", "config")
