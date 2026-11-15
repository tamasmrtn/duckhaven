"""Migration 0063 adds ``queries.dispatched_at``.

Drives 0063's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0062``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0063_query_dispatched_at.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0063", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


@pytest.fixture
def conn(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO queries VALUES ('q1')"))
        _run(connection, migration_module)
        yield connection


def _columns(conn) -> dict:
    return {c["name"]: c for c in inspect(conn).get_columns("queries")}


def test_revision_follows_0062(migration_module):
    assert migration_module.revision == "0063"
    assert migration_module.down_revision == "0062"


def test_existing_runs_are_left_without_a_dispatch_time(conn):
    assert _columns(conn)["dispatched_at"]["nullable"] is True
    assert conn.execute(text("SELECT dispatched_at FROM queries")).scalar_one() is None


def test_downgrade_drops_the_column(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert "dispatched_at" not in _columns(conn)
