"""Make column-level lineage recordable

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-16

0032 created ``lineage_column_edges`` empty, expecting that a writer and a
serializer would be all that column-level lineage ever needed. Two things it
cannot express turned out to matter, and both are added here. Nothing existing
is rewritten and the child table is still empty, so there is no data migration.

``lineage_edges.column_lineage`` is the load-bearing one. Column lineage's whole
value over the table graph is that a source referenced only in a ``WHERE`` clause
contributes *no* column relationships — that is how the graph stops
over-reporting. But "we worked out the columns and the honest answer is none" and
"we could not work out the columns at all" both look like an edge with no
children, and those two mean opposite things to somebody deciding whether it is
safe to drop a column. Existing rows become ``unknown``, which is exactly what
they are: nothing ever tried.

``first_seen_at``/``last_seen_at`` on the child exist because re-observing an
edge *accumulates* column mappings rather than replacing them — two statements
can legitimately write the same target from the same source through different
columns, and deleting whatever the newest statement did not mention would make
the pair flap. So a mapping that stops being asserted ages out through the same
``lineage_stale_after_days`` window the parent edge already uses, instead of
silently going on claiming a flow that no longer happens.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lineage_edges",
        sa.Column(
            "column_lineage",
            sa.String(16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "lineage_column_edges",
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "lineage_column_edges",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # "What feeds this column?" — the direction the column detail panel asks in.
    # The existing unique constraint already indexes the (edge_id, source_column)
    # prefix, so only the target side needs one.
    op.create_index(
        "ix_lineage_column_edges_edge_target",
        "lineage_column_edges",
        ["edge_id", "target_column"],
    )


def downgrade() -> None:
    op.drop_index("ix_lineage_column_edges_edge_target", table_name="lineage_column_edges")
    op.drop_column("lineage_column_edges", "last_seen_at")
    op.drop_column("lineage_column_edges", "first_seen_at")
    op.drop_column("lineage_edges", "column_lineage")
