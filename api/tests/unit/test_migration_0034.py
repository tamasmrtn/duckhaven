"""Migration 0034 withdraws the lineage backfill's schema, and nothing else.

Driven against a bare SQLite connection through an Alembic operations context,
like ``test_migration_0033``. 0033 runs first so 0034 is exercised against the
state it will actually meet, rather than against a hand-built approximation of
it.

The property worth pinning down is the *boundary*: 0034 must take away
``lineage_backfills`` and ``ix_queries_workspace_started`` while leaving
``table_metadata.table_uuid`` and ``ix_table_metadata_uuid`` alone. Those are how
a renamed table is told from a dropped one recreated under the same name, they
were merely delivered by the same revision, and losing them would silently break
rename survival.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_module():
    return _load("migration_0034", VERSIONS / "0034_drop_lineage_backfill.py")


@pytest.fixture
def previous_module():
    return _load("migration_0033", VERSIONS / "0033_lineage_backfill_and_identity.py")


def _prerequisites(conn):
    conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
    conn.execute(
        text(
            "CREATE TABLE table_metadata ("
            "  id CHAR(32) PRIMARY KEY,"
            "  catalog_id CHAR(32),"
            "  schema_name VARCHAR(255),"
            "  table_name VARCHAR(255)"
            ")"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE queries ("
            "  id CHAR(32) PRIMARY KEY,"
            "  workspace_id CHAR(32),"
            "  started_at DATETIME"
            ")"
        )
    )


def _rebind(module, conn):
    module.op = Operations(MigrationContext.configure(conn))


def test_revision_links_to_0033(migration_module):
    assert migration_module.revision == "0034"
    assert migration_module.down_revision == "0033"


def test_upgrade_removes_only_the_backfill(migration_module, previous_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _prerequisites(conn)
        _rebind(previous_module, conn)
        previous_module.upgrade()

        _rebind(migration_module, conn)
        migration_module.upgrade()

        after = inspect(conn)
        assert "lineage_backfills" not in set(after.get_table_names())
        assert "ix_queries_workspace_started" not in {
            i["name"] for i in after.get_indexes("queries")
        }

        # Rename detection was delivered by the same revision and has nothing to
        # do with the backfill. It stays.
        assert "table_uuid" in {c["name"] for c in after.get_columns("table_metadata")}
        assert "ix_table_metadata_uuid" in {i["name"] for i in after.get_indexes("table_metadata")}


def test_downgrade_restores_what_it_dropped(migration_module, previous_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _prerequisites(conn)
        _rebind(previous_module, conn)
        previous_module.upgrade()

        _rebind(migration_module, conn)
        migration_module.upgrade()
        _rebind(migration_module, conn)
        migration_module.downgrade()

        after = inspect(conn)
        assert "lineage_backfills" in set(after.get_table_names())
        uniques = {
            u["name"]: u["column_names"] for u in after.get_unique_constraints("lineage_backfills")
        }
        assert uniques["uq_lineage_backfills_workspace"] == ["workspace_id"]
        history = {i["name"]: i["column_names"] for i in after.get_indexes("queries")}
        assert history["ix_queries_workspace_started"] == ["workspace_id", "started_at", "id"]
