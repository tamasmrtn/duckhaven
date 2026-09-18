"""Migration 0042 adds the catalog-kind axis.

Drives 0042's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context (the full chain can't run on SQLite), mirroring
``test_migration_0032``. A minimal ``catalogs`` table is created first, seeded
with a pre-existing row, so the backfill has something to act on.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0042_catalog_kind.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0042", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(conn, rows=(("c1", "raw", "raw"),)):
    """A catalogs table shaped like 0041, plus pre-existing rows."""
    conn.execute(
        text(
            "CREATE TABLE catalogs ("
            "  id CHAR(32) PRIMARY KEY,"
            "  slug VARCHAR(255) NOT NULL UNIQUE,"
            "  name VARCHAR(255) NOT NULL,"
            "  polaris_name VARCHAR(255) NOT NULL UNIQUE,"
            "  storage_backend_id CHAR(32) NOT NULL)"
        )
    )
    for cid, slug, polaris_name in rows:
        conn.execute(
            text(
                "INSERT INTO catalogs (id, slug, name, polaris_name, storage_backend_id) "
                "VALUES (:id, :slug, :slug, :pn, 'sb1')"
            ),
            {"id": cid, "slug": slug, "pn": polaris_name},
        )


def test_revision_links_to_0041(migration_module):
    assert migration_module.revision == "0042"
    assert migration_module.down_revision == "0041"


def test_upgrade_adds_the_columns(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        columns = {c["name"]: c for c in inspect(conn).get_columns("catalogs")}
        assert "kind" in columns
        assert "metadata_schema" in columns
        assert columns["kind"]["nullable"] is False
        assert columns["metadata_schema"]["nullable"] is True


def test_upgrade_backfills_existing_catalogs_as_iceberg(migration_module):
    """Every pre-existing catalog becomes Iceberg + Polaris, behaviourally unchanged."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn, rows=(("c1", "raw", "raw"), ("c2", "curated", "curated")))
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        rows = conn.execute(text("SELECT slug, kind, metadata_schema FROM catalogs")).all()
        assert sorted(rows) == [
            ("curated", "iceberg_polaris", None),
            ("raw", "iceberg_polaris", None),
        ]


def test_polaris_name_becomes_nullable(migration_module):
    """A DuckLake catalog has no Polaris warehouse to name."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        conn.execute(
            text(
                "INSERT INTO catalogs (id, slug, name, kind, metadata_schema, "
                "storage_backend_id) VALUES "
                "('c9', 'lake', 'lake', 'ducklake', 'cat_lake', 'sb1')"
            )
        )
        got = conn.execute(text("SELECT polaris_name FROM catalogs WHERE id = 'c9'")).scalar_one()
        assert got is None


def test_downgrade_restores_the_original_shape(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()
        migration_module.downgrade()

        columns = {c["name"] for c in inspect(conn).get_columns("catalogs")}
        assert "kind" not in columns
        assert "metadata_schema" not in columns
        assert conn.execute(text("SELECT count(*) FROM catalogs")).scalar_one() == 1


def test_downgrade_refuses_while_a_ducklake_catalog_exists(migration_module):
    """``metadata_schema`` is the only pointer to that catalog's metadata;
    dropping it would strand the schema, so fail loudly."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()
        conn.execute(
            text(
                "INSERT INTO catalogs (id, slug, name, kind, metadata_schema, "
                "storage_backend_id) VALUES "
                "('c9', 'lake', 'lake', 'ducklake', 'cat_lake', 'sb1')"
            )
        )

        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            migration_module.downgrade()


def test_check_constraint_rejects_a_catalog_with_neither_identity(migration_module):
    """Neither identity set is a catalog nobody can open; enforced in the schema."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO catalogs (id, slug, name, kind, storage_backend_id) "
                    "VALUES ('c8', 'broken', 'broken', 'ducklake', 'sb1')"
                )
            )


def test_check_constraint_rejects_a_catalog_claiming_both_identities(migration_module):
    """Both identities set means two sources of truth for where the tables live."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO catalogs (id, slug, name, kind, polaris_name, "
                    "metadata_schema, storage_backend_id) VALUES "
                    "('c7', 'both', 'both', 'iceberg_polaris', 'both', 'cat_both', 'sb1')"
                )
            )
