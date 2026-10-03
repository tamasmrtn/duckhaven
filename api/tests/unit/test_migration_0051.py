"""Migration 0051 adds the result cache.

Drives 0051's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0050``. The inserts leave
the new columns out on purpose: unit tests build their schema from the models,
so a default the migration forgot would otherwise only fail on Postgres.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0051_result_cache.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0051", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def _seed(conn):
    conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY, slug TEXT, name TEXT)"))
    conn.execute(text("CREATE TABLE agents (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY, sql TEXT)"))
    conn.execute(text("CREATE TABLE sql_sessions (id CHAR(32) PRIMARY KEY, status TEXT)"))


def test_revision_follows_0050(migration_module):
    assert migration_module.revision == "0051"
    assert migration_module.down_revision == "0050"


def test_upgrade_defaults_existing_and_new_rows_to_cache_on(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        conn.execute(text("INSERT INTO workspaces VALUES ('w1', 'old', 'Old')"))
        conn.execute(text("INSERT INTO sql_sessions VALUES ('s1', 'open')"))
        _run(conn, migration_module)

        conn.execute(text("INSERT INTO workspaces (id, slug, name) VALUES ('w2', 'new', 'New')"))
        conn.execute(text("INSERT INTO sql_sessions (id, status) VALUES ('s2', 'open')"))
        assert conn.execute(
            text("SELECT result_cache_enabled FROM workspaces ORDER BY id")
        ).scalars().all() == [1, 1]
        assert conn.execute(
            text("SELECT use_cache, cache_state FROM sql_sessions ORDER BY id")
        ).all() == [(1, None), (1, None)]

        conn.execute(text("INSERT INTO queries (id, sql) VALUES ('q1', 'SELECT 1')"))
        assert conn.execute(
            text("SELECT cache_status, result_source_query_id FROM queries")
        ).one() == (None, None)
        assert "result_cache_entries" in inspect(conn).get_table_names()


def test_downgrade_removes_everything(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")

        inspector = inspect(conn)
        assert "result_cache_entries" not in inspector.get_table_names()
        assert "cache_status" not in {c["name"] for c in inspector.get_columns("queries")}
        assert "result_cache_enabled" not in {
            c["name"] for c in inspector.get_columns("workspaces")
        }
        assert "use_cache" not in {c["name"] for c in inspector.get_columns("sql_sessions")}
