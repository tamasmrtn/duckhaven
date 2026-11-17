"""Migration 0065 adds the access audit log table.

Drives 0065's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0061``. The Postgres-only
trigger and txid default are covered by the integration suite.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0065_audit_events.py"

INSERT = (
    "INSERT INTO audit_events (id, category, action, actor_type{extra_cols}) "
    "VALUES ('{id}', '{category}', 'catalog_grant.create', 'user'{extra_vals})"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0065", MIGRATION)
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
        _run(connection, migration_module)
        yield connection


def _insert(conn, *, id="e1", category="access", extra_cols="", extra_vals=""):
    conn.execute(
        text(INSERT.format(id=id, category=category, extra_cols=extra_cols, extra_vals=extra_vals))
    )


def test_revision_follows_0064(migration_module):
    assert migration_module.revision == "0065"
    assert migration_module.down_revision == "0064"


def test_an_event_defaults_to_a_successful_version_1_change_stamped_now(conn):
    _insert(conn)
    row = conn.execute(
        text("SELECT event_time, event_version, outcome, self_change FROM audit_events")
    ).one()

    assert row.event_time is not None
    assert (row.event_version, row.outcome, row.self_change) == (1, "success", 0)


@pytest.mark.parametrize(
    ("cols", "vals"),
    [
        ("", ""),  # the category below is invalid
        (", outcome", ", 'maybe'"),
        (", auth_method", ", 'password'"),
    ],
)
def test_values_outside_their_sets_are_refused(conn, cols, vals):
    category = "nonsense" if not cols else "access"
    with pytest.raises(sa.exc.IntegrityError):
        _insert(conn, category=category, extra_cols=cols, extra_vals=vals)


def test_an_event_has_no_foreign_keys_so_it_outlives_what_it_names(conn):
    assert inspect(conn).get_foreign_keys("audit_events") == []


def test_the_lookups_the_read_api_filters_on_are_indexed(conn):
    indexed = {i["name"] for i in inspect(conn).get_indexes("audit_events")}

    assert {
        "ix_audit_events_time",
        "ix_audit_events_actor",
        "ix_audit_events_subject",
        "ix_audit_events_workspace",
        "ix_audit_events_action",
        "ix_audit_events_txid",
        "ix_audit_events_self_change",
    } <= indexed


def test_downgrade_drops_the_table(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    assert "audit_events" not in inspect(conn).get_table_names()
