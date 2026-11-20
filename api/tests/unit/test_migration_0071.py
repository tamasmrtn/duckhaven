"""Migration 0071 indexes History's duration sort and text search, on Postgres only.

Its indexes are Postgres expressions and a ``pg_trgm`` index, so on SQLite both
directions are no-ops; ``tests/integration/test_history_indexes.py`` checks that
Postgres uses them.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0071_queries_history_indexes.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0071", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_revision_follows_0070(migration_module):
    assert migration_module.revision == "0071"
    assert migration_module.down_revision == "0070"


def test_sqlite_is_left_alone_both_ways(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE queries (id CHAR(32) PRIMARY KEY, sql TEXT)"))
        _run(conn, migration_module)
        assert inspect(conn).get_indexes("queries") == []
        _run(conn, migration_module, "downgrade")
        assert inspect(conn).get_indexes("queries") == []


def test_the_indexed_expression_is_the_one_history_sorts_on(migration_module):
    """The index serves the sort only while both spell the same expression."""
    from sqlalchemy.dialects import postgresql

    from api.services.query_history import duration_expr

    compiled = str(duration_expr().compile(dialect=postgresql.dialect()))
    assert compiled.replace("queries.", "") == migration_module.DURATION
