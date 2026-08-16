"""Migration 0033 adds stable table identity.

Drives just 0033's ``upgrade``/``downgrade`` against a bare SQLite connection via
an Alembic operations context (the full chain can't run on SQLite), mirroring
``test_migration_0032``. Minimal ``workspaces``/``users``/``table_metadata``/
``queries`` tables are created first because 0033 references them.

The property most worth asserting is that every change is *additive*: an existing
deployment's lineage keeps working untouched, because nothing here rewrites a row
and the new column is nullable.

0033 also created ``lineage_backfills`` and ``ix_queries_workspace_started``,
which 0034 drops again once the backfill was withdrawn. Those are asserted in
``test_migration_0034`` rather than here, so this file covers only what survives.
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
    / "0033_lineage_backfill_and_identity.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0033", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_revision_links_to_0032(migration_module):
    assert migration_module.revision == "0033"
    assert migration_module.down_revision == "0032"


def test_upgrade_then_downgrade(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _prerequisites(conn)
        # A table_metadata row from before the migration, to show it survives.
        conn.execute(
            text(
                "INSERT INTO table_metadata (id, catalog_id, schema_name, table_name)"
                " VALUES ('a', 'c', 'analytics', 'dim')"
            )
        )

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        inspector = inspect(conn)
        meta_columns = {c["name"]: c for c in inspector.get_columns("table_metadata")}
        assert "table_uuid" in meta_columns
        # Nullable, so an existing deployment needs no data migration and old
        # rows simply do not participate in rename detection until observed.
        assert meta_columns["table_uuid"]["nullable"] is True
        preserved = conn.execute(
            text("SELECT table_name, table_uuid FROM table_metadata WHERE id = 'a'")
        ).one()
        assert preserved == ("dim", None)

        assert "ix_table_metadata_uuid" in {
            i["name"] for i in inspector.get_indexes("table_metadata")
        }

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.downgrade()

        after = inspect(conn)
        assert "table_uuid" not in {c["name"] for c in after.get_columns("table_metadata")}
