"""Migration 0035 makes column-level lineage recordable, additively.

Driven against a bare SQLite connection through an Alembic operations context,
like ``test_migration_0034``. 0032 runs first so 0035 meets the tables it will
actually meet.

Two properties are worth pinning down. First that the change is *additive*: the
whole point of 0032 having shipped ``lineage_column_edges`` empty was that this
revision would not have to migrate or reshape anything, so every column and index
0032 created must survive untouched. Second that ``column_lineage`` defaults to
``unknown`` on rows that predate it — which is not a placeholder but the truthful
answer, since nothing ever attempted to derive columns for them.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_module():
    return _load("migration_0035", VERSIONS / "0035_column_lineage.py")


@pytest.fixture
def previous_module():
    return _load("migration_0032", VERSIONS / "0032_lineage.py")


def _prerequisites(conn):
    conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE catalogs (id CHAR(32) PRIMARY KEY)"))


def _rebind(module, conn):
    module.op = Operations(MigrationContext.configure(conn))


def _upgraded(conn, previous_module, migration_module):
    _prerequisites(conn)
    _rebind(previous_module, conn)
    previous_module.upgrade()
    _rebind(migration_module, conn)
    migration_module.upgrade()


def test_revision_links_to_0034(migration_module):
    assert migration_module.revision == "0035"
    assert migration_module.down_revision == "0034"


def test_upgrade_adds_the_column_lineage_state(migration_module, previous_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, previous_module, migration_module)

        columns = {c["name"] for c in inspect(conn).get_columns("lineage_edges")}
        assert "column_lineage" in columns


def test_existing_edges_default_to_unknown(migration_module, previous_module):
    """A row written before this revision never had columns attempted for it.

    ``unknown`` is the honest answer for those, and it has to come from the
    server default rather than from application code, because the rows already
    exist by the time any application code runs again.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _prerequisites(conn)
        _rebind(previous_module, conn)
        previous_module.upgrade()

        conn.execute(
            text(
                "INSERT INTO lineage_edges ("
                "  id, source_key, source_schema, source_table,"
                "  target_key, target_schema, target_table, provider, confidence"
                ") VALUES ("
                "  'e1', 'cat:a/main/src', 'main', 'src',"
                "  'cat:a/main/tgt', 'main', 'tgt', 'execution', 'exact'"
                ")"
            )
        )

        _rebind(migration_module, conn)
        migration_module.upgrade()

        state = conn.execute(text("SELECT column_lineage FROM lineage_edges")).scalar_one()
        assert state == "unknown"


def test_upgrade_adds_child_timestamps_and_the_target_index(migration_module, previous_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, previous_module, migration_module)

        after = inspect(conn)
        columns = {c["name"] for c in after.get_columns("lineage_column_edges")}
        assert {"first_seen_at", "last_seen_at"} <= columns

        indexes = {i["name"]: i["column_names"] for i in after.get_indexes("lineage_column_edges")}
        assert indexes["ix_lineage_column_edges_edge_target"] == ["edge_id", "target_column"]


def test_upgrade_leaves_everything_0032_shipped_intact(migration_module, previous_module):
    """The additive claim, stated as an assertion rather than a comment."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, previous_module, migration_module)

        after = inspect(conn)
        assert {"lineage_edges", "lineage_column_edges"} <= set(after.get_table_names())

        child = {c["name"] for c in after.get_columns("lineage_column_edges")}
        assert {"id", "edge_id", "source_column", "target_column"} <= child

        uniques = {
            u["name"]: u["column_names"]
            for u in after.get_unique_constraints("lineage_column_edges")
        }
        assert uniques["uq_lineage_column_edges_identity"] == [
            "edge_id",
            "source_column",
            "target_column",
        ]

        edge_uniques = {
            u["name"]: u["column_names"] for u in after.get_unique_constraints("lineage_edges")
        }
        assert edge_uniques["uq_lineage_edges_identity"] == [
            "provider",
            "source_key",
            "target_key",
        ]

        assert "ix_lineage_column_edges_edge" in {
            i["name"] for i in after.get_indexes("lineage_column_edges")
        }


def test_downgrade_removes_only_what_it_added(migration_module, previous_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, previous_module, migration_module)
        _rebind(migration_module, conn)
        migration_module.downgrade()

        after = inspect(conn)
        assert "column_lineage" not in {c["name"] for c in after.get_columns("lineage_edges")}

        child = {c["name"] for c in after.get_columns("lineage_column_edges")}
        assert "first_seen_at" not in child
        assert "last_seen_at" not in child
        # ...but the table 0032 created is still standing.
        assert {"id", "edge_id", "source_column", "target_column"} <= child

        indexes = {i["name"] for i in after.get_indexes("lineage_column_edges")}
        assert "ix_lineage_column_edges_edge_target" not in indexes
        assert "ix_lineage_column_edges_edge" in indexes
