"""Migration 0062 adds the table that counts dashboard drafts saved over MCP.

Drives 0062's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0061``. Foreign keys are
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
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0062_dashboard_draft_saves.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0062", MIGRATION)
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
        connection.execute(text("CREATE TABLE dashboards (id CHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users VALUES ('agent')"))
        connection.execute(text("INSERT INTO workspaces VALUES ('ws')"))
        connection.execute(text("INSERT INTO dashboards VALUES ('d')"))
        _run(connection, migration_module)
        connection.execute(
            text(
                "INSERT INTO dashboard_draft_saves (id, workspace_id, user_id, dashboard_id) "
                "VALUES ('s1', 'ws', 'agent', 'd')"
            )
        )
        yield connection


def _count(conn) -> int:
    return conn.execute(text("SELECT count(*) FROM dashboard_draft_saves")).scalar_one()


def test_revision_follows_0061(migration_module):
    assert migration_module.revision == "0062"
    assert migration_module.down_revision == "0061"


def test_a_save_is_stamped_when_it_is_recorded(conn):
    assert conn.execute(text("SELECT saved_at FROM dashboard_draft_saves")).scalar_one()


def test_saves_are_found_by_person_in_a_workspace_newest_last(conn):
    (index,) = inspect(conn).get_indexes("dashboard_draft_saves")

    assert index["column_names"] == ["workspace_id", "user_id", "saved_at"]


@pytest.mark.parametrize("parent", ["users", "workspaces", "dashboards"])
def test_a_save_goes_with_its_person_its_workspace_and_its_dashboard(conn, parent):
    conn.execute(text(f"DELETE FROM {parent}"))

    assert _count(conn) == 0


def test_downgrade_drops_the_table(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert "dashboard_draft_saves" not in inspect(conn).get_table_names()
