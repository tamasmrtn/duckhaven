"""Add tables to assistant_tool_calls

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-20

Nullable JSON column holding the distinct catalog/schema/table refs a run_sql
call touched, so the assistant panel can render "open in Catalog" deep links.
Existing rows simply have null (no known refs).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assistant_tool_calls",
        sa.Column("tables", sa.JSON().with_variant(JSONB, "postgresql"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assistant_tool_calls", "tables")
