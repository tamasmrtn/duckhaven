"""Migration 0052 adds ``queries.active_schema``.

Drives 0052's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0051``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0052_query_active_schema.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0052", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_revision_follows_0051(migration_module):
    assert migration_module.revision == "0052"
    assert migration_module.down_revision == "0051"


def test_existing_and_new_rows_have_no_schema(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY, sql TEXT)"))
        conn.execute(text("INSERT INTO queries VALUES ('q1', 'SELECT 1')"))
        _run(conn, migration_module)
        conn.execute(text("INSERT INTO queries (id, sql) VALUES ('q2', 'SELECT 2')"))
        assert conn.execute(
            text("SELECT active_schema FROM queries ORDER BY id")
        ).scalars().all() == [None, None]


def test_downgrade_drops_the_column(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY, sql TEXT)"))
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")
        columns = {c["name"] for c in inspect(conn).get_columns("queries")}
        assert "active_schema" not in columns
