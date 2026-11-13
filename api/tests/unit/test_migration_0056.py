"""Migration 0056 adds ``workspaces.timezone``.

Drives 0056's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0055``. The insert leaves
the new column out on purpose: unit tests build their schema from the models, so a
server default the migration forgot would otherwise only fail on Postgres.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0056_workspace_timezone.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0056", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_revision_follows_0055(migration_module):
    assert migration_module.revision == "0056"
    assert migration_module.down_revision == "0055"


def test_every_workspace_keeps_time_in_utc_until_an_owner_says_otherwise(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY, slug VARCHAR(255))"))
        conn.execute(text("INSERT INTO workspaces VALUES ('w1', 'old')"))
        _run(conn, migration_module)

        conn.execute(text("INSERT INTO workspaces (id, slug) VALUES ('w2', 'new')"))
        rows = conn.execute(text("SELECT timezone FROM workspaces ORDER BY id")).scalars().all()
        assert rows == ["UTC", "UTC"]

        _run(conn, migration_module, "downgrade")
        assert "timezone" not in {c["name"] for c in inspect(conn).get_columns("workspaces")}
