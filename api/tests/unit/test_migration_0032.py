"""Migration 0032 creates the lineage graph tables.

Drives just 0032's ``upgrade``/``downgrade`` against a bare SQLite connection via
an Alembic operations context (the full chain can't run on SQLite), mirroring
``test_migration_0030``. Minimal ``catalogs``/``workspaces`` tables are created
first because 0032's foreign keys point at them.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0032_lineage.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0032", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_links_to_0031(migration_module):
    assert migration_module.revision == "0032"
    assert migration_module.down_revision == "0031"


def test_upgrade_then_downgrade(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE catalogs (id CHAR(32) PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"))

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()

        inspector = inspect(conn)
        assert {"lineage_edges", "lineage_column_edges"} <= set(inspector.get_table_names())

        edge_columns = {c["name"] for c in inspector.get_columns("lineage_edges")}
        assert {
            "source_key",
            "source_catalog_id",
            "source_system",
            "source_schema",
            "source_table",
            "target_key",
            "target_catalog_id",
            "target_system",
            "target_schema",
            "target_table",
            "provider",
            "provider_run_id",
            "workspace_id",
            "operation",
            "confidence",
            "last_query_id",
            "first_seen_at",
            "last_seen_at",
            "observation_count",
        } <= edge_columns

        indexes = {i["name"] for i in inspector.get_indexes("lineage_edges")}
        assert {
            "ix_lineage_edges_source_key",
            "ix_lineage_edges_target_key",
            "ix_lineage_edges_provider_run",
            "ix_lineage_edges_source_catalog",
            "ix_lineage_edges_target_catalog",
        } <= indexes

        # The dedup key carries `provider`, which is what lets two producers
        # assert the same pair without either clobbering the other.
        uniques = {
            u["name"]: u["column_names"] for u in inspector.get_unique_constraints("lineage_edges")
        }
        assert uniques["uq_lineage_edges_identity"] == ["provider", "source_key", "target_key"]

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.downgrade()
        remaining = set(inspect(conn).get_table_names())
        assert not {"lineage_edges", "lineage_column_edges"} & remaining
