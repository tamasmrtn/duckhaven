"""Service-account PAT hashed storage

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-03

Adds hashed-at-rest storage for Personal Access Tokens (PATs) on the existing
``credentials`` table. PATs (``kind="pat"``) are higher-value, longer-lived
credentials than session cookies, so — unlike sessions/agent tokens, which keep
storing the raw value in ``token`` — a PAT stores only the SHA-256 hex digest of
its secret in the new ``token_hash`` column (with ``token`` NULL). The unique
index on ``token_hash`` gives an O(1) lookup by the hash of a presented token.

``token`` is relaxed to nullable so PAT rows can carry a hash without a raw
value; session/agent-bootstrap credentials are unaffected (they keep writing
``token``). No ``users`` change is needed for the new
``auth_provider="service_account"`` value — the column is a permissive
``String(50)`` with no CHECK constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credentials", sa.Column("token_hash", sa.String(64), nullable=True))
    op.create_index("ix_credentials_token_hash", "credentials", ["token_hash"], unique=True)
    op.alter_column("credentials", "token", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    op.alter_column("credentials", "token", existing_type=sa.String(255), nullable=False)
    op.drop_index("ix_credentials_token_hash", table_name="credentials")
    op.drop_column("credentials", "token_hash")
