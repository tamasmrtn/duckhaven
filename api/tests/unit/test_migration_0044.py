"""Migration 0044 records what a maintenance apply did.

Drives 0044's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0043``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0044_maintenance_apply.py"
)

_APPLY_COLUMNS = {
    "apply_status",
    "apply_error",
    "apply_result",
    "applied_at",
    "applied_by",
    "applied_query_id",
}


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0044", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(conn):
    conn.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
    conn.execute(
        text(
            "CREATE TABLE maintenance_recommendation ("
            "  id CHAR(32) PRIMARY KEY,"
            "  kind VARCHAR(40) NOT NULL,"
            "  status VARCHAR(20) NOT NULL)"
        )
    )
    conn.execute(
        text("INSERT INTO maintenance_recommendation (id, kind, status) VALUES ('r1','x','open')")
    )


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_upgrade_adds_the_apply_record(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        columns = {c["name"] for c in inspect(conn).get_columns("maintenance_recommendation")}
        assert _APPLY_COLUMNS <= columns


def test_downgrade_refuses_once_an_apply_has_happened(migration_module):
    """These columns and the query they point at are the only record that
    DuckHaven rewrote or deleted data files."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        conn.execute(
            text("UPDATE maintenance_recommendation SET apply_status = 'succeeded' WHERE id = 'r1'")
        )
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            _run(conn, migration_module, "downgrade")


def test_downgrade_drops_the_columns_when_nothing_was_applied(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")

        columns = {c["name"] for c in inspect(conn).get_columns("maintenance_recommendation")}
        assert not (_APPLY_COLUMNS & columns)


def test_revision_links_to_0043(migration_module):
    assert migration_module.revision == "0044"
    assert migration_module.down_revision == "0043"
