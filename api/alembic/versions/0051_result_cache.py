"""Result cache

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-03

``result_cache_entries`` holds one finished result per query identity in a
workspace, with the version of every table it was computed from: a repeated read
is answered from it while each of those is unchanged. Small results are copied
into ``inline_parquet``; larger ones stay in the agent's result file, addressed by
``source_query_id``.

``queries`` gains what the cache did with each run (``cache_status`` and a reason
in ``cache_detail``), the pre-dispatch table versions a miss is admitted against,
and, for a hit, the run whose result it served.

``workspaces.result_cache_enabled`` and ``sql_sessions.use_cache`` are the
workspace and session opt-outs, both on by default; ``sql_sessions.cache_state``
tracks what about a held connection decides whether a statement may use the
cache. All additive: existing rows read as "the cache never looked at this".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "result_cache_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("context", _JSON, nullable=False),
        sa.Column("tables", _JSON, nullable=False),
        sa.Column(
            "source_query_id",
            sa.Uuid(),
            sa.ForeignKey("queries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage", sa.String(16), nullable=False),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("inline_parquet", sa.LargeBinary(), nullable=True),
        sa.Column("result_bytes", sa.BigInteger(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("result_schema", _JSON, nullable=True),
        sa.Column("runtime_id", sa.String(32), nullable=True),
        sa.Column("compute_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hard_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "key_hash", name="uq_result_cache_workspace_key"),
    )
    op.create_index("ix_result_cache_expires_at", "result_cache_entries", ["expires_at"])
    op.create_index("ix_result_cache_source_query", "result_cache_entries", ["source_query_id"])

    with op.batch_alter_table("queries") as batch:
        batch.add_column(sa.Column("cache_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("cache_detail", sa.String(64), nullable=True))
        batch.add_column(sa.Column("cache_key_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("cache_versions", _JSON, nullable=True))
        batch.add_column(sa.Column("result_source_query_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_queries_result_source_query",
            "queries",
            ["result_source_query_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.add_column(
        "workspaces",
        sa.Column("result_cache_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "sql_sessions",
        sa.Column("use_cache", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("sql_sessions", sa.Column("cache_state", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("sql_sessions", "cache_state")
    op.drop_column("sql_sessions", "use_cache")
    op.drop_column("workspaces", "result_cache_enabled")
    with op.batch_alter_table("queries") as batch:
        batch.drop_constraint("fk_queries_result_source_query", type_="foreignkey")
        batch.drop_column("result_source_query_id")
        batch.drop_column("cache_versions")
        batch.drop_column("cache_key_hash")
        batch.drop_column("cache_detail")
        batch.drop_column("cache_status")
    op.drop_index("ix_result_cache_source_query", table_name="result_cache_entries")
    op.drop_index("ix_result_cache_expires_at", table_name="result_cache_entries")
    op.drop_table("result_cache_entries")
