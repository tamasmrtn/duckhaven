"""Migration 0070 indexes the foreign keys to agents and drops a duplicate index.

Drives 0070's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0068``.
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
    / "0070_agent_foreign_key_indexes.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0070", MIGRATION)
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
    columns: dict[str, set[str]] = {}
    for table, column in migration_module.FOREIGN_KEYS:
        columns.setdefault(table, set()).add(column)
    with engine.begin() as connection:
        for table, cols in columns.items():
            extra = ", token VARCHAR(255) UNIQUE" if table == "credentials" else ""
            body = ", ".join(f"{c} CHAR(32)" for c in sorted(cols))
            connection.execute(
                text(f"CREATE TABLE {table} (id CHAR(32) PRIMARY KEY, {body}{extra})")
            )
        connection.execute(text("CREATE INDEX ix_credentials_token ON credentials (token)"))
        _run(connection, migration_module)
        yield connection


def _indexes(conn, table) -> dict[str, list[str]]:
    return {i["name"]: i["column_names"] for i in inspect(conn).get_indexes(table)}


def test_revision_follows_0069(migration_module):
    assert migration_module.revision == "0070"
    assert migration_module.down_revision == "0069"


def test_every_foreign_key_to_agents_is_indexed_where_set(conn, migration_module):
    for table, column in migration_module.FOREIGN_KEYS:
        name = f"ix_{table}_{column}"
        assert _indexes(conn, table)[name] == [column]
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name}
        ).scalar_one()
        assert f"WHERE {column} IS NOT NULL" in sql


def test_the_duplicate_token_index_is_dropped(conn):
    assert "ix_credentials_token" not in _indexes(conn, "credentials")


def test_downgrade_restores_the_previous_indexes(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert "ix_credentials_token" in _indexes(conn, "credentials")
    for table, column in migration_module.FOREIGN_KEYS:
        assert f"ix_{table}_{column}" not in _indexes(conn, table)
