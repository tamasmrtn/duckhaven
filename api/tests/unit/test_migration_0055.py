"""Migration 0055 adds ``queries.probe``.

Drives 0055's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0054``. The insert leaves the
new column out on purpose: unit tests build their schema from the models, so a
default the migration forgot would otherwise only fail on Postgres.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0055_query_probe.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0055", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_revision_follows_0054(migration_module):
    assert migration_module.revision == "0055"
    assert migration_module.down_revision == "0054"


def test_upgrade_adds_a_nullable_probe_and_downgrade_drops_it(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY, sql TEXT)"))
        conn.execute(text("INSERT INTO queries VALUES ('q1', 'SELECT 1')"))
        _run(conn, migration_module)

        conn.execute(text("INSERT INTO queries (id, sql) VALUES ('q2', 'SELECT 2')"))
        assert conn.execute(text("SELECT probe FROM queries ORDER BY id")).scalars().all() == [
            None,
            None,
        ]

        _run(conn, migration_module, "downgrade")
        assert "probe" not in {c["name"] for c in inspect(conn).get_columns("queries")}
