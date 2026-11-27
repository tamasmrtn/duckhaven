"""Migration 0072 adds ``assistant_tool_calls.from_failed_turn``.

Drives 0072's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0052``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0072_assistant_tool_call_failed_turn.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0072", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_revision_follows_0071(migration_module):
    assert migration_module.revision == "0072"
    assert migration_module.down_revision == "0071"


def test_existing_rows_came_from_saved_turns_and_new_ones_default_the_same(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE assistant_tool_calls (id CHAR(32) PRIMARY KEY, tool TEXT)"))
        conn.execute(text("INSERT INTO assistant_tool_calls VALUES ('a', 'run_sql')"))
        _run(conn, migration_module)
        conn.execute(text("INSERT INTO assistant_tool_calls (id, tool) VALUES ('b', 'run_sql')"))
        conn.execute(
            text(
                "INSERT INTO assistant_tool_calls (id, tool, from_failed_turn) "
                "VALUES ('c', 'run_sql', 1)"
            )
        )

        rows = conn.execute(
            text("SELECT id, from_failed_turn FROM assistant_tool_calls ORDER BY id")
        ).all()

    assert [(r[0], bool(r[1])) for r in rows] == [("a", False), ("b", False), ("c", True)]


def test_the_column_cannot_be_null(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE assistant_tool_calls (id CHAR(32) PRIMARY KEY, tool TEXT)"))
        _run(conn, migration_module)
        column = next(
            c
            for c in inspect(conn).get_columns("assistant_tool_calls")
            if c["name"] == "from_failed_turn"
        )

    assert column["nullable"] is False


def test_downgrade_drops_the_column(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE assistant_tool_calls (id CHAR(32) PRIMARY KEY, tool TEXT)"))
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")
        columns = {c["name"] for c in inspect(conn).get_columns("assistant_tool_calls")}

    assert "from_failed_turn" not in columns
