"""Migration 0068 indexes assistant turns and tool calls by when they were written.

Drives 0068's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0067``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0068_assistant_created_index.py"
)
EXPECTED = {
    "assistant_messages": ("ix_assistant_messages_created_at", ["created_at", "id"]),
    "assistant_tool_calls": ("ix_assistant_tool_calls_created_at", ["created_at", "id"]),
}


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0068", MIGRATION)
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
        for table, (_, columns) in EXPECTED.items():
            connection.execute(text(f"CREATE TABLE {table} ({', '.join(columns)})"))
        _run(connection, migration_module)
        yield connection


def _indexes(conn, table: str) -> dict[str, list[str]]:
    return {i["name"]: i["column_names"] for i in inspect(conn).get_indexes(table)}


def test_revision_follows_0067(migration_module):
    assert migration_module.revision == "0068"
    assert migration_module.down_revision == "0067"


def test_each_table_is_indexed_by_creation_time_then_id(conn):
    for table, (name, columns) in EXPECTED.items():
        assert _indexes(conn, table)[name] == columns


def test_downgrade_drops_the_indexes(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    for table in EXPECTED:
        assert _indexes(conn, table) == {}
