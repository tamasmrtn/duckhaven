"""Merge local_fs/nas storage kinds into object_store

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # local_fs and nas were always functionally identical: both prefix labels
    # under the bundled MinIO bucket. Collapse them into one honest kind.
    # root_uri is left untouched so each workspace's Polaris base location is
    # byte-for-byte stable.
    op.execute(
        "UPDATE storage_backends SET kind = 'object_store' WHERE kind IN ('local_fs', 'nas')"
    )


def downgrade() -> None:
    # Best-effort and lossy: the local_fs/nas split was meaningless and cannot
    # be recovered, so everything maps back to local_fs.
    op.execute("UPDATE storage_backends SET kind = 'local_fs' WHERE kind = 'object_store'")
