"""Migration 0015 creates the catalog-migration tables.

The full chain can't run on SQLite (0001 uses postgresql.JSONB), so this drives
just 0015's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context and asserts the three tables appear and disappear.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0015_catalog_migrations.py"
)

_TABLES = {"catalog_migrations", "catalog_migration_tables", "catalog_migration_events"}


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0015", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_links_to_0014(migration_module):
    assert migration_module.revision == "0015"
    assert migration_module.down_revision == "0014"


def test_upgrade_then_downgrade(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()
        assert _TABLES <= set(inspect(conn).get_table_names())

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.downgrade()
        assert _TABLES.isdisjoint(set(inspect(conn).get_table_names()))
