"""Migration 0059 adds the dashboard tables.

Drives 0059's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0058``. Foreign keys are
switched on, so deleting a parent shows what each key does.
"""

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0059_dashboards.py"
TABLES = ("dashboards", "dashboard_revisions", "dashboard_refs", "dashboard_aliases")


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0059", MIGRATION)
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
        connection.execute(text("INSERT INTO users VALUES ('owner')"))
        connection.execute(text("INSERT INTO workspaces VALUES ('ws')"))
        _run(connection, migration_module)
        yield connection


def _dashboard(conn, id_: str, name: str, **values) -> None:
    row = {
        "id": id_,
        "workspace_id": "ws",
        "name": name,
        "title": name,
        "labels": "[]",
        "spec_version": 1,
        "created_by": "owner",
        **values,
    }
    conn.execute(
        text(
            f"INSERT INTO dashboards ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"
        ),
        row,
    )


def test_revision_follows_0058(migration_module):
    assert migration_module.revision == "0059"
    assert migration_module.down_revision == "0058"


def test_a_new_dashboard_is_a_draft_from_the_ui_at_version_one(conn):
    _dashboard(conn, "d1", "sales")

    row = conn.execute(text("SELECT status, source, version, updated_via FROM dashboards")).one()
    assert tuple(row) == ("draft", "ui", 1, "rest")


def test_names_are_unique_per_workspace_ignoring_case(conn):
    _dashboard(conn, "d1", "sales")

    with pytest.raises(sa.exc.IntegrityError):
        _dashboard(conn, "d2", "SALES")


@pytest.mark.parametrize(
    ("column", "value"), [("status", "live"), ("source", "svn"), ("updated_via", "fax")]
)
def test_enumerated_columns_refuse_other_values(conn, column, value):
    with pytest.raises(sa.exc.IntegrityError):
        _dashboard(conn, "d1", "sales", **{column: value})


def test_a_revision_number_is_used_once_per_dashboard(conn):
    _dashboard(conn, "d1", "sales")
    insert = text(
        "INSERT INTO dashboard_revisions (id, dashboard_id, number, spec, spec_hash) "
        "VALUES (:id, 'd1', 1, :spec, 'h')"
    )
    conn.execute(insert, {"id": "r1", "spec": json.dumps({})})

    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(insert, {"id": "r2", "spec": json.dumps({})})


def test_deleting_a_dashboard_takes_its_history_refs_and_aliases(conn):
    _dashboard(conn, "d1", "sales")
    conn.execute(
        text(
            "INSERT INTO dashboard_revisions (id, dashboard_id, number, spec, spec_hash) "
            "VALUES ('r1', 'd1', 1, '{}', 'h')"
        )
    )
    conn.execute(
        text(
            "INSERT INTO dashboard_refs (id, dashboard_id, workspace_id, state, kind, name) "
            "VALUES ('f1', 'd1', 'ws', 'published', 'model', 'orders')"
        )
    )
    conn.execute(
        text(
            "INSERT INTO dashboard_aliases (id, workspace_id, dashboard_id, name) "
            "VALUES ('a1', 'ws', 'd1', 'old-sales')"
        )
    )

    conn.execute(text("DELETE FROM dashboards WHERE id = 'd1'"))

    for table in TABLES[1:]:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_deleting_a_user_keeps_their_dashboards(conn):
    _dashboard(conn, "d1", "sales", updated_by="owner", published_by="owner")

    conn.execute(text("DELETE FROM users WHERE id = 'owner'"))

    row = conn.execute(text("SELECT created_by, updated_by, published_by FROM dashboards")).one()
    assert tuple(row) == (None, None, None)


def test_deleting_a_workspace_takes_its_dashboards(conn):
    _dashboard(conn, "d1", "sales")

    conn.execute(text("DELETE FROM workspaces WHERE id = 'ws'"))

    assert conn.execute(text("SELECT count(*) FROM dashboards")).scalar_one() == 0


def test_downgrade_drops_every_table(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert not set(TABLES) & set(inspect(conn).get_table_names())
