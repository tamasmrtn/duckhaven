"""Migration 0061 adds the dashboard proposals table.

Drives 0061's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0060``. Foreign keys are
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
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0061_dashboard_proposals.py"
)

INSERT = (
    "INSERT INTO dashboard_proposals "
    "(id, workspace_id, dashboard_id, created_by, summary, operations, base_version, "
    "base_hash, result_spec, result_hash, changes, expires_at{status}) "
    "VALUES ('{id}', 'ws', 'd', 'writer', 'Add orders.', '[]', 1, 'h', '{{}}', 'r', '[]', "
    "'2026-11-16 12:00:00'{value})"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0061", MIGRATION)
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
        connection.execute(text("INSERT INTO users VALUES ('writer')"))
        connection.execute(text("INSERT INTO workspaces VALUES ('ws')"))
        connection.execute(text("INSERT INTO dashboards VALUES ('d')"))
        _run(connection, migration_module)
        connection.execute(text(INSERT.format(id="p1", status="", value="")))
        yield connection


def _count(conn) -> int:
    return conn.execute(text("SELECT count(*) FROM dashboard_proposals")).scalar_one()


def test_revision_follows_0060(migration_module):
    assert migration_module.revision == "0061"
    assert migration_module.down_revision == "0060"


def test_a_proposal_is_pending_and_stamped_when_it_is_made(conn):
    row = conn.execute(text("SELECT status, created_at, decided_at FROM dashboard_proposals")).one()

    assert row.status == "pending" and row.created_at is not None and row.decided_at is None


def test_a_status_outside_the_three_is_refused(conn):
    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(text(INSERT.format(id="p2", status=", status", value=", 'stale'")))


@pytest.mark.parametrize("parent", ["users", "workspaces", "dashboards"])
def test_a_proposal_goes_with_its_person_its_workspace_and_its_dashboard(conn, parent):
    conn.execute(text(f"DELETE FROM {parent}"))

    assert _count(conn) == 0


def test_downgrade_drops_the_table(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert "dashboard_proposals" not in inspect(conn).get_table_names()
