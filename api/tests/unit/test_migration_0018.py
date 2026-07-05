"""Migration 0018 creates the assistant tables.

Drives just 0018's ``upgrade``/``downgrade`` against a bare SQLite connection via
an Alembic operations context (the full chain can't run on SQLite) and asserts the
three tables appear and disappear.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0018_assistant_conversations.py"
)

_TABLES = {"assistant_conversations", "assistant_messages", "assistant_tool_calls"}


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0018", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_links_to_0017(migration_module):
    assert migration_module.revision == "0018"
    assert migration_module.down_revision == "0017"


def test_upgrade_then_downgrade(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.upgrade()
        assert _TABLES <= set(inspect(conn).get_table_names())

        migration_module.op = Operations(MigrationContext.configure(conn))
        migration_module.downgrade()
        assert _TABLES.isdisjoint(set(inspect(conn).get_table_names()))
