"""Migration 0067 indexes the tables the system catalog copies by their time column.

Drives 0067's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0066``.
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
    / "0067_system_catalog_feed_indexes.py"
)
EXPECTED = {
    "agent_lifecycle_event": ("ix_agent_lifecycle_event_at", ["at", "id"]),
    "agent_metrics_minute": ("ix_agent_metrics_minute_minute", ["minute", "agent_id"]),
    "sql_sessions": ("ix_sql_sessions_created_at", ["created_at", "id"]),
    "table_health_sample": ("ix_table_health_sample_scanned_at", ["scanned_at", "id"]),
}


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0067", MIGRATION)
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


def test_revision_follows_0066(migration_module):
    assert migration_module.revision == "0067"
    assert migration_module.down_revision == "0066"


def test_each_table_is_indexed_by_its_time_then_its_key(conn):
    for table, (name, columns) in EXPECTED.items():
        assert _indexes(conn, table)[name] == columns


def test_downgrade_drops_the_indexes(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    for table in EXPECTED:
        assert _indexes(conn, table) == {}
