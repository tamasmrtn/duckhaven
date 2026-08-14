"""Migration 0033 adds backfill state, stable table identity, and a history index.

Drives just 0033's ``upgrade``/``downgrade`` against a bare SQLite connection via
an Alembic operations context (the full chain can't run on SQLite), mirroring
``test_migration_0032``. Minimal ``workspaces``/``users``/``table_metadata``/
``queries`` tables are created first because 0033 references them.

The property most worth asserting is that every change is *additive*: an existing
deployment's lineage keeps working untouched, because nothing here rewrites a row
and both new columns are nullable.
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
    / "0033_lineage_backfill_and_identity.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0033", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prerequisites(conn):
    conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
    conn.execute(
        text(
            "CREATE TABLE table_metadata ("
            "  id CHAR(32) PRIMARY KEY,"
            "  catalog_id CHAR(32),"
            "  schema_name VARCHAR(255),"
            "  table_name VARCHAR(255)"
            ")"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE queries ("
            "  id CHAR(32) PRIMARY KEY,"
            "  workspace_id CHAR(32),"
            "  started_at DATETIME"
            ")"
        )
    )


def test_revision_links_to_0032(migration_module):
    assert migration_module.revision == "0033"
    assert migration_module.down_revision == "0032"


def test_upgrade_then_downgrade(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _prerequisites(conn)
        # A table_metadata row from before the migration, to show it survives.
        conn.execute(
            text(
                "INSERT INTO table_metadata (id, catalog_id, schema_name, table_name)"
                " VALUES ('a', 'c', 'analytics', 'dim')"
            )
        )

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        inspector = inspect(conn)
        assert "lineage_backfills" in set(inspector.get_table_names())

        columns = {c["name"]: c for c in inspector.get_columns("lineage_backfills")}
        assert {
            "workspace_id",
            "status",
            "dry_run",
            "since_at",
            "covered_from",
            "covered_through",
            "cursor_started_at",
            "cursor_query_id",
            "queries_scanned",
            "queries_with_lineage",
            "queries_skipped",
            "parse_failures",
            "queries_failed",
            "edges_created",
            "edges_updated",
            "cancel_requested",
            "error",
            "requested_by",
            "created_at",
            "started_at",
            "finished_at",
        } <= set(columns)

        # One backfill per workspace: a second request adjusts the row rather
        # than racing a duplicate walk over the same history.
        uniques = {
            u["name"]: u["column_names"]
            for u in inspector.get_unique_constraints("lineage_backfills")
        }
        assert uniques["uq_lineage_backfills_workspace"] == ["workspace_id"]

        meta_columns = {c["name"]: c for c in inspector.get_columns("table_metadata")}
        assert "table_uuid" in meta_columns
        # Nullable, so an existing deployment needs no data migration and old
        # rows simply do not participate in rename detection until observed.
        assert meta_columns["table_uuid"]["nullable"] is True
        preserved = conn.execute(
            text("SELECT table_name, table_uuid FROM table_metadata WHERE id = 'a'")
        ).one()
        assert preserved == ("dim", None)

        assert "ix_table_metadata_uuid" in {
            i["name"] for i in inspector.get_indexes("table_metadata")
        }
        history = {i["name"]: i["column_names"] for i in inspector.get_indexes("queries")}
        assert history["ix_queries_workspace_started"] == ["workspace_id", "started_at", "id"]

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.downgrade()

        after = inspect(conn)
        assert "lineage_backfills" not in set(after.get_table_names())
        assert "table_uuid" not in {c["name"] for c in after.get_columns("table_metadata")}
        assert "ix_queries_workspace_started" not in {
            i["name"] for i in after.get_indexes("queries")
        }
