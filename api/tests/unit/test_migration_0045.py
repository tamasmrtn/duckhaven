"""Migration 0045 records where a DuckLake catalog's data came from and went.

Drives 0045's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0044``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0045_ducklake_migration_paths.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0045", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(conn):
    conn.execute(
        text(
            "CREATE TABLE catalog_migrations ("
            "  id CHAR(32) PRIMARY KEY,"
            "  status VARCHAR(20) NOT NULL)"
        )
    )
    conn.execute(text("INSERT INTO catalog_migrations (id, status) VALUES ('m1','completed')"))


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_upgrade_adds_both_paths(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        columns = {c["name"] for c in inspect(conn).get_columns("catalog_migrations")}
        assert {"source_data_path", "target_data_path"} <= columns


def test_downgrade_refuses_while_source_data_is_still_retained(migration_module):
    """It is the only pointer to the data kept for rollback, so dropping it
    strands that prefix in object storage with nothing referencing it."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        conn.execute(
            text("UPDATE catalog_migrations SET source_data_path = 's3://b/old/' WHERE id = 'm1'")
        )
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            _run(conn, migration_module, "downgrade")


def test_downgrade_drops_them_once_the_sweep_has_run(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")

        columns = {c["name"] for c in inspect(conn).get_columns("catalog_migrations")}
        assert not ({"source_data_path", "target_data_path"} & columns)


def test_revision_links_to_0044(migration_module):
    assert migration_module.revision == "0045"
    assert migration_module.down_revision == "0044"
