"""Migration 0030 adds the columns that let work park while compute starts.

Drives just 0030's ``upgrade``/``downgrade`` against a bare SQLite connection via
an Alembic operations context (the full chain can't run on SQLite), mirroring
``test_migration_0019``. Minimal ``sql_sessions``/``queries``/``agents`` tables are
created first because 0030 only alters them.
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
    / "0030_pending_compute_admission.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0030", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_links_to_0029(migration_module):
    assert migration_module.revision == "0030"
    assert migration_module.down_revision == "0029"


def test_upgrade_then_downgrade(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE agents (id CHAR(32) PRIMARY KEY)"))
        conn.execute(
            text("CREATE TABLE sql_sessions (id CHAR(32) PRIMARY KEY, status VARCHAR(20))")
        )
        conn.execute(text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY)"))

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()
        sessions = {c["name"] for c in inspect(conn).get_columns("sql_sessions")}
        assert {"requested_agent_id", "opening_at"} <= sessions
        assert "requested_agent_id" in {c["name"] for c in inspect(conn).get_columns("queries")}
        assert "ix_sql_sessions_pending" in {
            i["name"] for i in inspect(conn).get_indexes("sql_sessions")
        }

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.downgrade()
        sessions = {c["name"] for c in inspect(conn).get_columns("sql_sessions")}
        assert not {"requested_agent_id", "opening_at"} & sessions
        assert "requested_agent_id" not in {c["name"] for c in inspect(conn).get_columns("queries")}
