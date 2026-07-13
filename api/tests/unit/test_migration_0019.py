"""Migration 0019 adds the SQL session table + queries.session_id.

Drives just 0019's ``upgrade``/``downgrade`` against a bare SQLite connection via
an Alembic operations context (the full chain can't run on SQLite). A minimal
``queries`` table is created first because 0019 adds a column to it.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0019_sql_sessions.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0019", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_links_to_0018(migration_module):
    assert migration_module.revision == "0019"
    assert migration_module.down_revision == "0018"


def test_upgrade_then_downgrade(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        # 0019 references these tables (FKs + the queries ALTER); stand up minimal
        # versions first (the full chain that creates them can't run on SQLite).
        for tbl in ("workspaces", "agents", "users", "queries"):
            conn.execute(text(f"CREATE TABLE {tbl} (id CHAR(32) PRIMARY KEY)"))

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()
        assert "sql_sessions" in set(inspect(conn).get_table_names())
        assert "session_id" in {c["name"] for c in inspect(conn).get_columns("queries")}

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.downgrade()
        assert "sql_sessions" not in set(inspect(conn).get_table_names())
        assert "session_id" not in {c["name"] for c in inspect(conn).get_columns("queries")}
