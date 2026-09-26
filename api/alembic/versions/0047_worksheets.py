"""Server-side worksheets and saved-query audit columns

Revision ID: 0047
Revises: 0046
Create Date: 2026-09-25

Worksheets move out of the browser: each tab is a private, autosaved row that
may link to a shared saved query. ``version`` guards content edits from two
windows overwriting each other.

Saved queries gain ``updated_at``/``updated_by`` (the principal scheduled runs
execute as) and a case-insensitive unique name per workspace. Existing duplicate
names are renamed to ``name (2)``, ``name (3)``, ... — never deleted, because a
schedule cascades with its saved query. Downgrade does not restore those names.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME_MAX = 255


def _dedupe_saved_query_names(bind) -> None:
    saved = sa.table(
        "saved_queries",
        sa.column("id"),
        sa.column("workspace_id"),
        sa.column("name"),
        sa.column("created_at"),
    )
    rows = bind.execute(
        sa.select(saved.c.id, saved.c.workspace_id, saved.c.name).order_by(
            saved.c.workspace_id, saved.c.created_at, saved.c.id
        )
    ).all()

    taken: dict[object, set[str]] = {}
    for row in rows:
        taken.setdefault(row.workspace_id, set()).add(row.name.lower())

    seen: dict[object, set[str]] = {}
    for row in rows:
        kept = seen.setdefault(row.workspace_id, set())
        key = row.name.lower()
        if key not in kept:
            kept.add(key)
            continue
        names = taken[row.workspace_id]
        n = 2
        while True:
            suffix = f" ({n})"
            candidate = row.name[: NAME_MAX - len(suffix)] + suffix
            if candidate.lower() not in names:
                break
            n += 1
        names.add(candidate.lower())
        kept.add(candidate.lower())
        bind.execute(saved.update().where(saved.c.id == row.id).values(name=candidate))


def upgrade() -> None:
    op.create_table(
        "worksheets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(NAME_MAX), nullable=False, server_default="Untitled"),
        sa.Column("sql", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("catalog", sa.String(NAME_MAX), nullable=True),
        sa.Column("timeout_s", sa.Float(), nullable=True),
        sa.Column(
            "saved_query_id",
            sa.Uuid(),
            sa.ForeignKey("saved_queries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_query_id", sa.Uuid(), nullable=True),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tab_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_worksheets_owner_ws_open", "worksheets", ["owner_id", "workspace_id", "is_open"]
    )
    op.create_index(
        "ix_worksheets_owner_ws_updated", "worksheets", ["owner_id", "workspace_id", "updated_at"]
    )
    op.create_index("ix_worksheets_saved_query_id", "worksheets", ["saved_query_id"])

    with op.batch_alter_table("saved_queries") as batch:
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("updated_by", sa.Uuid(), nullable=True))

    bind = op.get_bind()
    saved = sa.table(
        "saved_queries",
        sa.column("created_at"),
        sa.column("created_by"),
        sa.column("updated_at"),
        sa.column("updated_by"),
    )
    bind.execute(
        saved.update().values(updated_at=saved.c.created_at, updated_by=saved.c.created_by)
    )

    with op.batch_alter_table("saved_queries") as batch:
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("updated_by", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key("fk_saved_queries_updated_by", "users", ["updated_by"], ["id"])

    _dedupe_saved_query_names(bind)
    op.create_index(
        "uq_saved_queries_ws_lower_name",
        "saved_queries",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_saved_queries_ws_lower_name", table_name="saved_queries")
    with op.batch_alter_table("saved_queries") as batch:
        batch.drop_constraint("fk_saved_queries_updated_by", type_="foreignkey")
        batch.drop_column("updated_by")
        batch.drop_column("updated_at")

    op.drop_index("ix_worksheets_saved_query_id", table_name="worksheets")
    op.drop_index("ix_worksheets_owner_ws_updated", table_name="worksheets")
    op.drop_index("ix_worksheets_owner_ws_open", table_name="worksheets")
    op.drop_table("worksheets")
