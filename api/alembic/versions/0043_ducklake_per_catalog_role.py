"""Per-catalog PostgreSQL role for DuckLake catalogs

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-21

One shared ``ducklake_agent`` login reached every catalog's metadata schema, so
the only thing stopping an agent from reading or corrupting another catalog's
metadata was the SQL denylist -- a control that depends on a parser's fidelity.
Each catalog now has its own login, which makes the isolation structural.

The password lives in ``credentials`` rather than on ``catalogs``: that table is
serialized to every client on every catalog listing, and a live secret does not
belong there. ``catalog_id`` is unique because a catalog has exactly one.

This migration does the *data* half only. Alembic is connected to the
``duckhaven`` database, and the role and its grants live in ``ducklake``, which
it has no connection to. The roles themselves are created by
``DuckLakeCatalogBackend.ensure()`` on next browse, or immediately by
``POST /api/admin/catalogs/ducklake/reconcile-roles``.

Downgrade refuses while any such credential exists: dropping the column strands
the catalog-to-role mapping, leaving roles in PostgreSQL that DuckHaven can no
longer name, and therefore no longer drop.
"""

import secrets
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill(bind: sa.engine.Connection) -> None:
    """Mint a credential for every catalog that predates this.

    Lightweight `sa.table()` constructs with string literals, so the backfill
    runs under SQLite in the unit suite as well as Postgres -- the house pattern
    from 0042.
    """
    # Typed columns, unlike 0042's: this backfill binds real UUID values, and an
    # untyped sa.column() hands them to the driver unadapted.
    catalogs = sa.table("catalogs", sa.column("id", sa.Uuid()), sa.column("kind"))
    credentials = sa.table(
        "credentials",
        sa.column("id", sa.Uuid()),
        sa.column("kind"),
        sa.column("token"),
        sa.column("catalog_id", sa.Uuid()),
    )
    existing = {
        row[0]
        for row in bind.execute(
            sa.select(credentials.c.catalog_id).where(credentials.c.kind == "ducklake_role")
        )
    }
    rows = bind.execute(sa.select(catalogs.c.id).where(catalogs.c.kind == "ducklake")).fetchall()
    for (catalog_id,) in rows:
        if catalog_id in existing:
            continue
        bind.execute(
            credentials.insert().values(
                id=uuid.uuid4(),
                kind="ducklake_role",
                token=secrets.token_urlsafe(32),
                catalog_id=catalog_id,
            )
        )


def upgrade() -> None:
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("catalog_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_credentials_catalog_id", "catalogs", ["catalog_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_unique_constraint("uq_credentials_catalog_id", ["catalog_id"])

    _backfill(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    credentials = sa.table("credentials", sa.column("kind"))
    remaining = bind.execute(
        sa.select(sa.func.count())
        .select_from(credentials)
        .where(credentials.c.kind == "ducklake_role")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"Refusing to downgrade: {remaining} DuckLake catalog role credential(s) exist. "
            "catalog_id is the only mapping from a catalog to its PostgreSQL role, so "
            "dropping it would leave roles DuckHaven can no longer name or drop. Drop those "
            "catalogs first, then downgrade."
        )

    with op.batch_alter_table("credentials") as batch:
        batch.drop_constraint("uq_credentials_catalog_id", type_="unique")
        batch.drop_constraint("fk_credentials_catalog_id", type_="foreignkey")
        batch.drop_column("catalog_id")
