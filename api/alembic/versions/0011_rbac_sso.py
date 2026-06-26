"""RBAC roles/permissions and federated-identity user columns

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-26

Adds the first-class RBAC model (``roles`` + ``role_permissions``, seeded with
the built-in ``admin``/``user`` roles) and the columns that let a user
authenticate through an external IdP (OIDC/LDAP) instead of a local password.

Rollback caveat: ``downgrade`` re-asserts ``users.password_hash NOT NULL``. If
any federated (null-password) users have been created, delete or assign them a
password before downgrading, or the column alter will fail.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Permission values mirror api.services.permissions.Permission. Kept as a static
# snapshot here so the migration never depends on evolving app code.
_ADMIN_PERMISSIONS = [
    "agents:manage",
    "storage:manage",
    "users:manage",
    "maintenance:manage",
    "catalogs:admin",
    "queries:admin",
]


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission"),
    )

    # Seed the two built-in roles. `admin` gets every permission; `user` gets none
    # (workspace roles are enforced separately).
    roles_tbl = sa.table(
        "roles",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_system", sa.Boolean),
    )
    perms_tbl = sa.table(
        "role_permissions",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission", sa.String),
    )
    admin_id = uuid.uuid4()
    user_id = uuid.uuid4()
    op.bulk_insert(
        roles_tbl,
        [
            {
                "id": admin_id,
                "name": "admin",
                "description": "Built-in admin role",
                "is_system": True,
            },
            {
                "id": user_id,
                "name": "user",
                "description": "Built-in user role",
                "is_system": True,
            },
        ],
    )
    op.bulk_insert(
        perms_tbl,
        [{"role_id": admin_id, "permission": p} for p in _ADMIN_PERMISSIONS],
    )

    # Federated-identity columns on users.
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)
    op.add_column(
        "users",
        sa.Column("auth_provider", sa.String(50), nullable=False, server_default="local"),
    )
    op.add_column("users", sa.Column("external_subject", sa.String(255), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(
        "ix_users_provider_subject",
        "users",
        ["auth_provider", "external_subject"],
        unique=True,
        postgresql_where=sa.text("external_subject IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_provider_subject", table_name="users")
    op.drop_column("users", "is_active")
    op.drop_column("users", "external_subject")
    op.drop_column("users", "auth_provider")
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=False)
    op.drop_table("role_permissions")
    op.drop_table("roles")
