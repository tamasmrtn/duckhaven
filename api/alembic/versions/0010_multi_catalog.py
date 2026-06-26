"""Decouple catalogs from workspaces (M:N) and move storage onto catalogs

Introduces ``catalogs`` + ``workspace_catalogs`` (M:N), backfills one default
catalog per existing workspace (keeping its Polaris name = the workspace slug so
no Polaris catalog is renamed), re-scopes ``table_metadata`` to the catalog, adds
``catalog_id`` to the maintenance tables, and drops ``workspaces.storage_backend_id``
(storage is now catalog-scoped — the I4 change).

The data backfill lives in ``_backfill`` so it can be exercised on SQLite (the
full chain can't run there; see test_migration_0007).

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-22
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLUG_RE = re.compile(r"[^a-z0-9_]")


def _catalog_slug(base: str, taken: set[str]) -> str:
    s = _SLUG_RE.sub("_", base.lower())
    if not s or not s[0].isalpha():
        s = f"c_{s}".rstrip("_")
    s = s[:240] or "catalog"
    candidate, n = s, 1
    while candidate in taken:
        n += 1
        candidate = f"{s}_{n}"
    taken.add(candidate)
    return candidate


def _backfill(bind: sa.engine.Connection) -> None:
    """Create one default catalog per workspace and point the metadata at it."""
    workspaces = sa.table(
        "workspaces",
        sa.column("id"),
        sa.column("slug"),
        sa.column("name"),
        sa.column("storage_backend_id"),
    )
    members = sa.table(
        "workspace_members", sa.column("workspace_id"), sa.column("user_id"), sa.column("role")
    )
    users = sa.table("users", sa.column("id"))
    catalogs = sa.table(
        "catalogs",
        sa.column("id"),
        sa.column("slug"),
        sa.column("name"),
        sa.column("polaris_name"),
        sa.column("storage_backend_id"),
        sa.column("created_by"),
        sa.column("created_at"),
    )
    workspace_catalogs = sa.table(
        "workspace_catalogs",
        sa.column("workspace_id"),
        sa.column("catalog_id"),
        sa.column("is_default"),
        sa.column("attached_at"),
        sa.column("attached_by"),
    )
    # String-typed literals so the backfill is portable: psycopg adapts these to
    # uuid/timestamptz on Postgres, and SQLite (the migration test) can bind them
    # against the lightweight, type-less ``sa.column`` constructs above.
    now = datetime.now(tz=UTC).isoformat()
    any_user = bind.execute(sa.select(users.c.id).limit(1)).scalar()
    taken: set[str] = set()

    for ws in bind.execute(
        sa.select(
            workspaces.c.id, workspaces.c.slug, workspaces.c.name, workspaces.c.storage_backend_id
        )
    ).fetchall():
        owner = bind.execute(
            sa.select(members.c.user_id)
            .where(members.c.workspace_id == ws.id, members.c.role == "owner")
            .limit(1)
        ).scalar()
        created_by = owner or any_user
        catalog_id = str(uuid.uuid4())
        bind.execute(
            sa.insert(catalogs).values(
                id=catalog_id,
                slug=_catalog_slug(ws.slug, taken),
                name=ws.name,
                polaris_name=ws.slug,
                storage_backend_id=ws.storage_backend_id,
                created_by=created_by,
                created_at=now,
            )
        )
        bind.execute(
            sa.insert(workspace_catalogs).values(
                workspace_id=ws.id,
                catalog_id=catalog_id,
                is_default=True,
                attached_at=now,
                attached_by=created_by,
            )
        )
        for tbl in ("table_metadata", "table_health_sample", "maintenance_recommendation"):
            t = sa.table(tbl, sa.column("workspace_id"), sa.column("catalog_id"))
            bind.execute(
                sa.update(t).where(t.c.workspace_id == ws.id).values(catalog_id=catalog_id)
            )


def upgrade() -> None:
    op.create_table(
        "catalogs",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("polaris_name", sa.String(255), nullable=False),
        sa.Column("storage_backend_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["storage_backend_id"], ["storage_backends.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        sa.UniqueConstraint("polaris_name"),
    )
    op.create_table(
        "workspace_catalogs",
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_id", UUID(as_uuid=True), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("attached_by", UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["catalog_id"], ["catalogs.id"]),
        sa.ForeignKeyConstraint(["attached_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("workspace_id", "catalog_id"),
    )

    # Nullable catalog_id columns, then backfill, then enforce + swap keys.
    op.add_column("table_metadata", sa.Column("catalog_id", UUID(as_uuid=True), nullable=True))
    op.add_column("table_health_sample", sa.Column("catalog_id", UUID(as_uuid=True), nullable=True))
    op.add_column(
        "maintenance_recommendation", sa.Column("catalog_id", UUID(as_uuid=True), nullable=True)
    )

    _backfill(op.get_bind())

    with op.batch_alter_table("table_metadata") as b:
        b.alter_column("catalog_id", nullable=False)
        b.drop_constraint("uq_table_metadata_ident", type_="unique")
        b.drop_column("workspace_id")
        b.create_foreign_key("fk_table_metadata_catalog", "catalogs", ["catalog_id"], ["id"])
        b.create_unique_constraint(
            "uq_table_metadata_ident", ["catalog_id", "schema_name", "table_name"]
        )

    with op.batch_alter_table("table_health_sample") as b:
        b.alter_column("catalog_id", nullable=False)
        b.create_foreign_key("fk_table_health_sample_catalog", "catalogs", ["catalog_id"], ["id"])

    with op.batch_alter_table("maintenance_recommendation") as b:
        b.alter_column("catalog_id", nullable=False)
        b.drop_constraint("uq_maintenance_recommendation_ident_kind", type_="unique")
        b.create_foreign_key(
            "fk_maintenance_recommendation_catalog", "catalogs", ["catalog_id"], ["id"]
        )
        b.create_unique_constraint(
            "uq_maintenance_recommendation_ident_kind",
            ["catalog_id", "schema_name", "table_name", "kind"],
        )

    op.drop_column("workspaces", "storage_backend_id")


def downgrade() -> None:
    op.add_column("workspaces", sa.Column("storage_backend_id", UUID(as_uuid=True), nullable=True))
    # Best-effort: re-point each workspace at its default catalog's backend.
    bind = op.get_bind()
    workspaces = sa.table("workspaces", sa.column("id"), sa.column("storage_backend_id"))
    catalogs = sa.table("catalogs", sa.column("id"), sa.column("storage_backend_id"))
    wc = sa.table(
        "workspace_catalogs",
        sa.column("workspace_id"),
        sa.column("catalog_id"),
        sa.column("is_default"),
    )
    for ws in bind.execute(sa.select(workspaces.c.id)).fetchall():
        backend = bind.execute(
            sa.select(catalogs.c.storage_backend_id)
            .select_from(wc.join(catalogs, catalogs.c.id == wc.c.catalog_id))
            .where(wc.c.workspace_id == ws.id, wc.c.is_default.is_(True))
            .limit(1)
        ).scalar()
        if backend is not None:
            bind.execute(
                sa.update(workspaces)
                .where(workspaces.c.id == ws.id)
                .values(storage_backend_id=backend)
            )

    with op.batch_alter_table("maintenance_recommendation") as b:
        b.drop_constraint("uq_maintenance_recommendation_ident_kind", type_="unique")
        b.drop_constraint("fk_maintenance_recommendation_catalog", type_="foreignkey")
        b.drop_column("catalog_id")
        b.create_unique_constraint(
            "uq_maintenance_recommendation_ident_kind",
            ["workspace_id", "schema_name", "table_name", "kind"],
        )
    with op.batch_alter_table("table_health_sample") as b:
        b.drop_constraint("fk_table_health_sample_catalog", type_="foreignkey")
        b.drop_column("catalog_id")
    with op.batch_alter_table("table_metadata") as b:
        b.drop_constraint("uq_table_metadata_ident", type_="unique")
        b.drop_constraint("fk_table_metadata_catalog", type_="foreignkey")
        b.add_column(sa.Column("workspace_id", UUID(as_uuid=True), nullable=True))
        b.drop_column("catalog_id")
        b.create_unique_constraint(
            "uq_table_metadata_ident", ["workspace_id", "schema_name", "table_name"]
        )

    op.drop_table("workspace_catalogs")
    op.drop_table("catalogs")
