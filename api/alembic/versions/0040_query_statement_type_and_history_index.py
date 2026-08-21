"""Add statement_type to queries and a composite index for history paging

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-21

Two changes that together let History filter and page server-side.

``statement_type`` is the coarse kind of statement a run executed (``select``,
``insert``, ``create``, …), classified from the SQL by sqlglot as the row is
written. Persisted rather than derived at read time because a derived value
cannot be filtered in SQL. Existing rows simply have null, which the type filter
reads as "unknown" rather than as any particular kind — null and the ``other``
bucket mean different things and the filter keeps them apart. There is no
backfill: classifying historical rows would mean re-parsing every statement in
Python inside the migration, and the SQL-only shortcut (``sql ILIKE 'select%'``)
misclassifies CTEs, leading comments and whitespace. A wrong type is worse than
no type.

``ix_queries_workspace_started_id`` supports the keyset pagination History now
uses **within one workspace**: the default ordering is ``started_at DESC, id
DESC``, and ``id`` is in the index because it is the tiebreaker that makes a
cursor deterministic when two runs share a timestamp. It does *not* help the
admin ``all_workspaces`` scope, which drops the leading column and so still
sorts the table; that path is admin-only and rare, and an index sized for it
would be paid for on every insert into the busiest table in the schema.

Note this is not a revert of revision 0034, which dropped
``ix_queries_workspace_started``: that index was ``(workspace_id, started_at)``
and served the old unpaged "most recent hundred" scan. This one is its
deliberately differently-shaped successor.

``ix_queries_workspace_id`` (revision 0001) is dropped because the new index
begins with the same column and answers everything the single-column one did.
Keeping both would mean maintaining two indexes per insert for one lookup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("queries", sa.Column("statement_type", sa.String(32), nullable=True))
    op.create_index(
        "ix_queries_workspace_started_id",
        "queries",
        ["workspace_id", sa.text("started_at DESC"), sa.text("id DESC")],
    )
    # Fully covered by the composite above, which shares its leading column.
    op.drop_index("ix_queries_workspace_id", table_name="queries")


def downgrade() -> None:
    op.create_index("ix_queries_workspace_id", "queries", ["workspace_id"])
    op.drop_index("ix_queries_workspace_started_id", table_name="queries")
    op.drop_column("queries", "statement_type")
