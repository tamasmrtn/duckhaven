"""Migration 0064 adds ``users.sidebar``.

Drives 0064's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0063``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0064_user_sidebar.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0064", MIGRATION)
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
        connection.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users VALUES ('u1')"))
        _run(connection, migration_module)
        yield connection


def _columns(conn) -> dict:
    return {c["name"]: c for c in inspect(conn).get_columns("users")}


def test_revision_follows_0063(migration_module):
    assert migration_module.revision == "0064"
    assert migration_module.down_revision == "0063"


def test_existing_users_have_not_chosen(conn):
    assert _columns(conn)["sidebar"]["nullable"] is True
    assert conn.execute(text("SELECT sidebar FROM users")).scalar_one() is None


def test_downgrade_drops_the_column(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert "sidebar" not in _columns(conn)
