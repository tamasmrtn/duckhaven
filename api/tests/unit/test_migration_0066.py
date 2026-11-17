"""Migration 0066 indexes finished queries by ``(finished_at, id)``.

Drives 0066's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0063``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0066_queries_finished_index.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0066", MIGRATION)
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
        connection.execute(
            text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY, finished_at DATETIME)")
        )
        _run(connection, migration_module)
        yield connection


def _index(conn) -> dict | None:
    return next(
        (i for i in inspect(conn).get_indexes("queries") if i["name"] == "ix_queries_finished"),
        None,
    )


def test_revision_follows_0065(migration_module):
    assert migration_module.revision == "0066"
    assert migration_module.down_revision == "0065"


def test_finished_runs_are_indexed_by_finish_time_then_id(conn):
    index = _index(conn)
    assert index["column_names"] == ["finished_at", "id"]
    sql = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE name = 'ix_queries_finished'")
    ).scalar_one()
    assert "WHERE finished_at IS NOT NULL" in sql


def test_downgrade_drops_the_index(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert _index(conn) is None
