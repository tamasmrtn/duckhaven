"""Migration 0060 adds the dashboard previews table.

Drives 0060's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0059``. Foreign keys are
switched on, so deleting a parent shows what each key does.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0060_dashboard_previews.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0060", MIGRATION)
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
    sa.event.listen(
        engine, "connect", lambda dbapi, _record: dbapi.execute("PRAGMA foreign_keys=ON")
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users VALUES ('writer')"))
        connection.execute(text("INSERT INTO workspaces VALUES ('ws')"))
        _run(connection, migration_module)
        connection.execute(
            text(
                "INSERT INTO dashboard_previews "
                "(id, workspace_id, created_by, spec, spec_hash, expires_at) "
                "VALUES ('p1', 'ws', 'writer', '{}', 'h', '2026-11-14 12:00:00')"
            )
        )
        yield connection


def _count(conn) -> int:
    return conn.execute(text("SELECT count(*) FROM dashboard_previews")).scalar_one()


def test_revision_follows_0059(migration_module):
    assert migration_module.revision == "0060"
    assert migration_module.down_revision == "0059"


def test_a_preview_is_stamped_when_it_is_made(conn):
    row = conn.execute(text("SELECT created_at, updated_at FROM dashboard_previews")).one()

    assert None not in tuple(row)


@pytest.mark.parametrize("parent", ["users", "workspaces"])
def test_a_preview_goes_with_its_creator_and_its_workspace(conn, parent):
    conn.execute(text(f"DELETE FROM {parent}"))

    assert _count(conn) == 0


def test_downgrade_drops_the_table(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert "dashboard_previews" not in inspect(conn).get_table_names()
