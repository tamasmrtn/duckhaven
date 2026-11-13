"""Migration 0058 records who a token and a run act for.

Drives 0058's ``upgrade``/``downgrade`` against a bare SQLite connection via an
Alembic operations context, mirroring ``test_migration_0057``. Foreign keys are
switched on, so deleting the person shows what each new key does: the token goes
with them, the run stays and forgets them.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0058_delegated_identity.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0058", MIGRATION)
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
    sa.event.listen(
        engine, "connect", lambda dbapi, _record: dbapi.execute("PRAGMA foreign_keys=ON")
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE credentials (id CHAR(32) PRIMARY KEY, "
                "user_id CHAR(32) REFERENCES users (id) ON DELETE CASCADE, kind VARCHAR(50))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE queries (id CHAR(32) PRIMARY KEY, "
                "user_id CHAR(32) REFERENCES users (id), sql TEXT)"
            )
        )
        connection.execute(text("INSERT INTO users VALUES ('account'), ('person')"))
        connection.execute(text("INSERT INTO credentials VALUES ('old', 'account', 'pat')"))
        connection.execute(text("INSERT INTO queries VALUES ('old', 'account', 'SELECT 1')"))
        _run(connection, migration_module)
        yield connection


def test_revision_follows_0057(migration_module):
    assert migration_module.revision == "0058"
    assert migration_module.down_revision == "0057"


def test_existing_tokens_and_runs_act_for_no_one(conn):
    assert conn.execute(text("SELECT on_behalf_of_id FROM credentials")).scalar_one() is None
    assert conn.execute(text("SELECT on_behalf_of_id FROM queries")).scalar_one() is None


def test_runs_are_indexed_by_who_they_were_for(conn):
    indexes = {i["name"]: i["column_names"] for i in inspect(conn).get_indexes("queries")}
    assert indexes["ix_queries_on_behalf_of_id"] == ["on_behalf_of_id"]


def test_deleting_the_person_takes_their_token_and_keeps_their_runs(conn):
    token, run = uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute(
        text(
            "INSERT INTO credentials (id, user_id, kind, on_behalf_of_id) "
            "VALUES (:id, 'account', 'pat', 'person')"
        ),
        {"id": token},
    )
    conn.execute(
        text(
            "INSERT INTO queries (id, user_id, sql, on_behalf_of_id) "
            "VALUES (:id, 'account', 'SELECT 1', 'person')"
        ),
        {"id": run},
    )

    conn.execute(text("DELETE FROM users WHERE id = 'person'"))

    tokens = conn.execute(text("SELECT id FROM credentials")).scalars().all()
    assert tokens == ["old"]
    kept = conn.execute(text("SELECT on_behalf_of_id FROM queries WHERE id = :id"), {"id": run})
    assert kept.scalar_one() is None


def test_downgrade_drops_both_columns(conn, migration_module):
    _run(conn, migration_module, "downgrade")

    for table in ("credentials", "queries"):
        assert "on_behalf_of_id" not in {c["name"] for c in inspect(conn).get_columns(table)}
    assert "ix_queries_on_behalf_of_id" not in {
        i["name"] for i in inspect(conn).get_indexes("queries")
    }
