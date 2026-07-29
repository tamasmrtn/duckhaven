"""Per-agent access control

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-29

Adds ``agent_grants`` plus ``agents.access_mode``: a per-agent ACL overlaying the
global ``agents:manage`` permission, which until now was the only thing that said
anything about who may touch an agent.

The fleet outgrew the global flag. An elastic agent is matched to demand by its
pool key -- the set of storage-backend kinds it supports -- so one agent genuinely
serves every workspace with the same storage shape. Cost, blast radius and data
proximity became per-agent concerns that a deployment-wide boolean cannot express.

``access_mode`` is why this is additive rather than a breaking change. Before this
migration there was no ``use``-side authorization at all: any authenticated caller
could list every agent and dispatch work to it. Deny-by-default would therefore
have taken working access away from every non-admin on upgrade. ``"open"`` (the
server default, so existing rows and any insert that forgets the column land there)
keeps exactly that behaviour, and ``"restricted"`` opts a single agent in. That is
the same shape as ``workspace_catalogs.access_mode`` (open/scoped), for the same
reason. A deployment-wide setting was rejected because it cannot lock down one
sensitive agent while the rest of the fleet stays shared, which is the actual case.

The principal is a user XOR a workspace rather than a polymorphic
``(principal_type, principal_id)`` pair, because the pair cannot carry a foreign
key -- and losing ``ON DELETE CASCADE`` would leave live grants pointing at deleted
users and workspaces. Two nullable FK columns with a CHECK keep referential
integrity on both sides. ``created_by`` is ``SET NULL`` instead, so deleting the
granter never deletes the grant.

The uniqueness constraints are *partial* indexes rather than the ``COALESCE`` trick
``catalog_grants`` uses: these are UUID columns, and coalescing one to ``''``
requires a Postgres-only ``::text`` cast that the SQLite test backend rejects.
Partial indexes behave identically on both.

No new ``Permission`` member is introduced, so ``role_permissions`` is untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("access_mode", sa.String(20), nullable=False, server_default="open"),
    )
    op.create_table(
        "agent_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (workspace_id IS NULL)",
            name="ck_agent_grants_one_principal",
        ),
    )
    op.create_index(
        "uq_agent_grants_user",
        "agent_grants",
        ["agent_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
        sqlite_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_agent_grants_workspace",
        "agent_grants",
        ["agent_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
        sqlite_where=sa.text("workspace_id IS NOT NULL"),
    )
    op.create_index("ix_agent_grants_user_id", "agent_grants", ["user_id"])
    op.create_index("ix_agent_grants_workspace_id", "agent_grants", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_grants_workspace_id", table_name="agent_grants")
    op.drop_index("ix_agent_grants_user_id", table_name="agent_grants")
    op.drop_index("uq_agent_grants_workspace", table_name="agent_grants")
    op.drop_index("uq_agent_grants_user", table_name="agent_grants")
    op.drop_table("agent_grants")
    op.drop_column("agents", "access_mode")
