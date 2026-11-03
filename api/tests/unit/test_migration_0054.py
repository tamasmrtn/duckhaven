"""Migration 0054 adds ``workspaces.default_agent_id``.

Drives 0054's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0051``. The inserts leave the
new column out on purpose: unit tests build their schema from the models, so a
default the migration forgot would otherwise only fail on Postgres.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0054_workspace_default_agent.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0054", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def _seed(conn):
    conn.execute(text("CREATE TABLE agents (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY, slug TEXT, name TEXT)"))


def test_revision_follows_0053(migration_module):
    assert migration_module.revision == "0054"
    assert migration_module.down_revision == "0053"


def test_upgrade_leaves_existing_and_new_workspaces_without_a_default(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        conn.execute(text("INSERT INTO workspaces VALUES ('w1', 'old', 'Old')"))
        _run(conn, migration_module)

        conn.execute(text("INSERT INTO workspaces (id, slug, name) VALUES ('w2', 'new', 'New')"))
        assert conn.execute(
            text("SELECT default_agent_id FROM workspaces ORDER BY id")
        ).scalars().all() == [None, None]


def test_upgrade_adds_a_set_null_foreign_key_to_agents(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        fks = inspect(conn).get_foreign_keys("workspaces")
        assert [(fk["referred_table"], fk["constrained_columns"]) for fk in fks] == [
            ("agents", ["default_agent_id"])
        ]
        assert fks[0]["options"].get("ondelete") == "SET NULL"


def test_downgrade_removes_the_column(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")

        assert "default_agent_id" not in {
            c["name"] for c in inspect(conn).get_columns("workspaces")
        }
