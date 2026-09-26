"""Migration 0047 adds worksheets and dedupes saved-query names.

Drives 0047's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0045``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0047_worksheets.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0047", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(conn):
    conn.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE agents (id CHAR(32) PRIMARY KEY)"))
    conn.execute(
        text(
            "CREATE TABLE saved_queries ("
            "  id CHAR(32) PRIMARY KEY,"
            "  workspace_id CHAR(32) NOT NULL REFERENCES workspaces(id),"
            "  name VARCHAR(255) NOT NULL,"
            "  sql TEXT NOT NULL,"
            "  default_agent_id CHAR(32) REFERENCES agents(id),"
            "  created_by CHAR(32) NOT NULL REFERENCES users(id),"
            "  created_at DATETIME NOT NULL,"
            "  last_run_at DATETIME)"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE schedules ("
            "  id CHAR(32) PRIMARY KEY,"
            "  saved_query_id CHAR(32) REFERENCES saved_queries(id) ON DELETE CASCADE)"
        )
    )
    conn.execute(text("INSERT INTO users VALUES ('u1'), ('u2')"))
    conn.execute(text("INSERT INTO workspaces VALUES ('w1'), ('w2')"))
    rows = [
        ("q1", "w1", "report", "u1", "2026-01-01"),
        ("q2", "w1", "Report", "u2", "2026-01-02"),
        ("q3", "w1", "report", "u1", "2026-01-03"),
        ("q4", "w1", "report (2)", "u1", "2026-01-04"),
        ("q5", "w2", "report", "u1", "2026-01-05"),
    ]
    for qid, ws, name, user, created in rows:
        conn.execute(
            text(
                "INSERT INTO saved_queries (id, workspace_id, name, sql, created_by, created_at) "
                "VALUES (:id, :ws, :name, 'SELECT 1', :user, :created)"
            ),
            {"id": qid, "ws": ws, "name": name, "user": user, "created": created},
        )
    conn.execute(text("INSERT INTO schedules VALUES ('s1', 'q3')"))


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def _names(conn):
    return dict(conn.execute(text("SELECT id, name FROM saved_queries")).all())


def test_upgrade_creates_worksheets(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        columns = {c["name"] for c in inspect(conn).get_columns("worksheets")}
        assert {
            "id",
            "workspace_id",
            "owner_id",
            "title",
            "sql",
            "agent_id",
            "catalog",
            "timeout_s",
            "saved_query_id",
            "last_query_id",
            "is_open",
            "tab_position",
            "version",
            "created_at",
            "updated_at",
        } <= columns


def test_upgrade_renames_duplicates_oldest_first(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        names = _names(conn)
        assert names["q1"] == "report"
        # "report (2)" is already taken by q4, so the renames skip past it.
        assert names["q2"] == "Report (3)"
        assert names["q3"] == "report (4)"
        assert names["q4"] == "report (2)"
        # Another workspace keeps its own "report".
        assert names["q5"] == "report"


def test_upgrade_keeps_schedules_on_renamed_rows(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        target = conn.execute(text("SELECT saved_query_id FROM schedules")).scalar_one()
        assert target == "q3"


def test_upgrade_backfills_audit_columns(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        row = conn.execute(
            text(
                "SELECT created_by, updated_by, created_at, updated_at FROM saved_queries "
                "WHERE id = 'q2'"
            )
        ).one()
        assert row.updated_by == row.created_by == "u2"
        assert row.updated_at == row.created_at


def test_unique_index_rejects_case_insensitive_duplicate(migration_module):
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        with conn.begin():
            _seed(conn)
            _run(conn, migration_module)
        with pytest.raises(IntegrityError), conn.begin():
            conn.execute(
                text(
                    "INSERT INTO saved_queries (id, workspace_id, name, sql, created_by, "
                    "created_at, updated_by, updated_at) VALUES ('q9', 'w1', 'REPORT', "
                    "'SELECT 1', 'u1', '2026-02-01', 'u1', '2026-02-01')"
                )
            )


def test_downgrade_drops_everything(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")

        inspector = inspect(conn)
        assert "worksheets" not in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("saved_queries")}
        assert not ({"updated_at", "updated_by"} & columns)


def test_revision_links_to_0046(migration_module):
    assert migration_module.revision == "0047"
    assert migration_module.down_revision == "0046"


MIGRATION_0048 = MIGRATION.with_name("0048_saved_query_updated_at_default.py")


def test_0048_lets_the_database_default_updated_at(migration_module):
    """0047 left updated_at NOT NULL with no default, so an insert that relied on
    the model's server default failed with a null violation on Postgres."""
    spec = importlib.util.spec_from_file_location("migration_0048", MIGRATION_0048)
    module_0048 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module_0048)
    assert (module_0048.revision, module_0048.down_revision) == ("0048", "0047")

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        _run(conn, module_0048)
        conn.execute(
            text(
                "INSERT INTO saved_queries (id, workspace_id, name, sql, created_by, "
                "created_at, updated_by) VALUES ('q9', 'w2', 'fresh', 'SELECT 1', 'u1', "
                "'2026-02-01', 'u1')"
            )
        )
        updated_at = conn.execute(
            text("SELECT updated_at FROM saved_queries WHERE id = 'q9'")
        ).scalar_one()
        assert updated_at is not None
        _run(conn, module_0048, "downgrade")
