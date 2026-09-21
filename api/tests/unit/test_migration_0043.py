"""Migration 0043 gives each DuckLake catalog its own PostgreSQL role credential.

Drives 0043's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0042``. Minimal
``catalogs`` and ``credentials`` tables are created first so the backfill has
something to act on.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0043_ducklake_per_catalog_role.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0043", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C1 = uuid.uuid4().hex
C2 = uuid.uuid4().hex


def _seed(conn, catalogs=((C1, "raw", "ducklake"), (C2, "berg", "iceberg_polaris"))):
    conn.execute(
        text(
            "CREATE TABLE catalogs ("
            "  id CHAR(32) PRIMARY KEY,"
            "  slug VARCHAR(255) NOT NULL UNIQUE,"
            "  kind VARCHAR(32) NOT NULL)"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE credentials ("
            "  id CHAR(32) PRIMARY KEY,"
            "  kind VARCHAR(50) NOT NULL,"
            "  token VARCHAR(255))"
        )
    )
    for cid, slug, kind in catalogs:
        conn.execute(
            text("INSERT INTO catalogs (id, slug, kind) VALUES (:i, :s, :k)"),
            {"i": cid, "s": slug, "k": kind},
        )


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_upgrade_adds_the_column_and_backfills_only_ducklake_catalogs(migration_module):
    """An Iceberg catalog has no PostgreSQL role, so it must not get a credential."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        assert "catalog_id" in {c["name"] for c in inspect(conn).get_columns("credentials")}
        rows = conn.execute(
            text("SELECT catalog_id, token FROM credentials WHERE kind = 'ducklake_role'")
        ).fetchall()
        assert [r[0] for r in rows] == [C1]
        # A real secret, not a placeholder anyone could guess.
        assert len(rows[0][1]) >= 32


def test_the_backfill_is_idempotent(migration_module):
    """Re-running must not mint a second login for a catalog that has one."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        first = conn.execute(text("SELECT token FROM credentials")).scalar_one()

        migration_module._backfill(conn)

        rows = conn.execute(text("SELECT token FROM credentials")).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == first


def test_downgrade_refuses_while_a_role_credential_exists(migration_module):
    """catalog_id is the only mapping from a catalog to its role; dropping it
    would leave roles in PostgreSQL that DuckHaven can no longer name or drop."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            _run(conn, migration_module, "downgrade")


def test_downgrade_drops_the_column_once_none_remain(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn, catalogs=((C2, "berg", "iceberg_polaris"),))
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")

        assert "catalog_id" not in {c["name"] for c in inspect(conn).get_columns("credentials")}


def test_revision_links_to_0042(migration_module):
    assert migration_module.revision == "0043"
    assert migration_module.down_revision == "0042"


def test_uuid_is_imported_for_the_backfill(migration_module):
    assert migration_module.uuid is uuid
