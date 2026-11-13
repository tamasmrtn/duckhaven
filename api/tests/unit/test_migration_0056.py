"""Migration 0056 adds how a metric is shown: format, unit, currency, polarity.

Drives 0056's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0055``. Existing metrics
must come through unset, and the CHECKs must refuse a value the API would.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0056_metric_presentation.py"
)
COLUMNS = ("format", "unit", "currency", "polarity")


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0056", MIGRATION)
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
        connection.execute(
            text("CREATE TABLE semantic_metrics (id CHAR(32) PRIMARY KEY, name VARCHAR(255))")
        )
        connection.execute(text("INSERT INTO semantic_metrics VALUES ('m1', 'revenue')"))
        _run(connection, migration_module)
        yield connection


def test_revision_follows_0055(migration_module):
    assert migration_module.revision == "0056"
    assert migration_module.down_revision == "0055"


def test_existing_metrics_come_through_unset(conn):
    row = conn.execute(text(f"SELECT {', '.join(COLUMNS)} FROM semantic_metrics")).one()
    assert tuple(row) == (None, None, None, None)


def test_a_known_format_and_polarity_are_accepted(conn):
    conn.execute(
        text(
            "INSERT INTO semantic_metrics (id, name, format, currency, polarity) "
            "VALUES ('m2', 'aov', 'currency', 'EUR', 'up_good')"
        )
    )


@pytest.mark.parametrize("column", ["format", "polarity"])
def test_an_unknown_value_is_refused(conn, column):
    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(
            text(f"INSERT INTO semantic_metrics (id, name, {column}) VALUES ('m3', 'x', 'bogus')")
        )


def test_downgrade_drops_the_columns(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE semantic_metrics (id CHAR(32) PRIMARY KEY, name VARCHAR(255))")
        )
        _run(connection, migration_module)
        _run(connection, migration_module, "downgrade")
        names = {c["name"] for c in inspect(connection).get_columns("semantic_metrics")}
        assert names.isdisjoint(COLUMNS)
