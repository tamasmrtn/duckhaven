"""Dashboards

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-13

Stored dashboard files.

- ``dashboards`` holds each dashboard's published file and its draft, with the
  title, description and labels copied out for listing. Names are unique per
  workspace, ignoring case. ``version`` guards against two editors overwriting
  each other.
- ``dashboard_revisions`` keeps every published file, numbered per dashboard.
- ``dashboard_refs`` lists the models, metrics, dimensions and tables each file
  reads, so a change to one can find the dashboards it breaks.
- ``dashboard_aliases`` keeps old names after a rename, so old links still work.

All four are new, so the migration changes no existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def _now() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _user(name: str) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


def _parent(name: str, table: str) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _parent("workspace_id", "workspaces"),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("labels", _JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("spec", _JSON, nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("spec_hash", sa.String(64), nullable=True),
        sa.Column("draft_spec", _JSON, nullable=True),
        sa.Column("draft_source_text", sa.Text(), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False, server_default="ui"),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _user("created_by"),
        _now(),
        _user("updated_by"),
        sa.Column("updated_via", sa.String(16), nullable=False, server_default="rest"),
        sa.Column("client", sa.String(255), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _user("published_by"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_dashboards_status"),
        sa.CheckConstraint("source IN ('ui', 'git')", name="ck_dashboards_source"),
        sa.CheckConstraint(
            "updated_via IN ('ui', 'rest', 'cli', 'assistant', 'mcp')",
            name="ck_dashboards_updated_via",
        ),
    )
    op.create_index(
        "uq_dashboards_ws_lower_name",
        "dashboards",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "dashboard_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _parent("dashboard_id", "dashboards"),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("spec", _JSON, nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        _user("published_by"),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_dashboard_revisions_number",
        "dashboard_revisions",
        ["dashboard_id", "number"],
        unique=True,
    )

    op.create_table(
        "dashboard_refs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _parent("dashboard_id", "dashboards"),
        _parent("workspace_id", "workspaces"),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("name", sa.String(767), nullable=False),
        sa.CheckConstraint("state IN ('draft', 'published')", name="ck_dashboard_refs_state"),
        sa.CheckConstraint(
            "kind IN ('model', 'metric', 'dimension', 'table')", name="ck_dashboard_refs_kind"
        ),
    )
    op.create_index(
        "ix_dashboard_refs_lookup", "dashboard_refs", ["workspace_id", "kind", "model", "name"]
    )
    op.create_index("ix_dashboard_refs_dashboard", "dashboard_refs", ["dashboard_id", "state"])

    op.create_table(
        "dashboard_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _parent("workspace_id", "workspaces"),
        _parent("dashboard_id", "dashboards"),
        sa.Column("name", sa.String(64), nullable=False),
        _now(),
    )
    op.create_index(
        "uq_dashboard_aliases_ws_lower_name",
        "dashboard_aliases",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index("ix_dashboard_aliases_dashboard", "dashboard_aliases", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("ix_dashboard_aliases_dashboard", table_name="dashboard_aliases")
    op.drop_index("uq_dashboard_aliases_ws_lower_name", table_name="dashboard_aliases")
    op.drop_table("dashboard_aliases")
    op.drop_index("ix_dashboard_refs_dashboard", table_name="dashboard_refs")
    op.drop_index("ix_dashboard_refs_lookup", table_name="dashboard_refs")
    op.drop_table("dashboard_refs")
    op.drop_index("uq_dashboard_revisions_number", table_name="dashboard_revisions")
    op.drop_table("dashboard_revisions")
    op.drop_index("uq_dashboards_ws_lower_name", table_name="dashboards")
    op.drop_table("dashboards")
