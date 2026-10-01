"""Migration 0050 adds coverage and OOM kills to the per-minute agent rollup.

Drives 0050's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0045``.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0050_agent_metrics_coverage.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0050", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module, direction="upgrade"):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def _seed(conn):
    conn.execute(
        text(
            "CREATE TABLE agent_metrics_minute ("
            "  agent_id CHAR(32), minute DATETIME, cpu_avg FLOAT NOT NULL,"
            "  PRIMARY KEY (agent_id, minute))"
        )
    )
    conn.execute(text("INSERT INTO agent_metrics_minute VALUES ('a', '2026-09-30', 1.0)"))


def test_revision_follows_0049(migration_module):
    assert migration_module.revision == "0050"
    assert migration_module.down_revision == "0049"


def test_upgrade_adds_nullable_columns_and_leaves_old_rows_unmeasured(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)

        columns = {c["name"]: c for c in inspect(conn).get_columns("agent_metrics_minute")}
        assert columns["covered_s"]["nullable"] and columns["oom_kills"]["nullable"]
        # Rows from before the upgrade were never measured: NULL, not zero.
        assert conn.execute(
            text("SELECT covered_s, oom_kills FROM agent_metrics_minute")
        ).one() == (None, None)


def test_downgrade_drops_them(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(conn, migration_module)
        _run(conn, migration_module, "downgrade")

        columns = {c["name"] for c in inspect(conn).get_columns("agent_metrics_minute")}
        assert "covered_s" not in columns and "oom_kills" not in columns
