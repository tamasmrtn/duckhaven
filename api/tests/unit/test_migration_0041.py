"""Migration 0041 creates the documentation search tables.

Driven against a bare SQLite connection through an Alembic operations context,
like ``test_migration_0036``.

The claim worth pinning is the dialect split. The tables must be created
everywhere, because the ORM maps them and the unit suite runs on SQLite; the
``search`` tsvector and its GIN index must be created *only* on Postgres,
because SQLite has no equivalent and a migration that fails there would take the
whole unit suite with it. The weighted expression is asserted as text rather
than executed for the same reason — the behaviour it produces is scored against
a real Postgres in ``api/tests/integration/test_docs_search.py``.
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
    return _load("migration_0041", VERSIONS / "0041_docs_pages_fts.py")


@pytest.fixture
def conn():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        yield connection


def _upgraded(conn, module):
    module.op = Operations(MigrationContext.configure(conn))
    module.upgrade()


def test_both_tables_are_created(conn, migration_module):
    _upgraded(conn, migration_module)

    tables = set(inspect(conn).get_table_names())

    assert {"docs_pages", "docs_corpus_meta"} <= tables


def test_pages_are_keyed_by_path(conn, migration_module):
    """Path is the identity: the corpus is replaced wholesale by path, and a
    duplicate would mean one page ranked twice."""
    _upgraded(conn, migration_module)

    pk = inspect(conn).get_pk_constraint("docs_pages")

    assert pk["constrained_columns"] == ["path"]


def test_the_tsvector_is_skipped_off_postgres(conn, migration_module):
    """SQLite has no tsvector. Creating it unconditionally would fail every unit
    test that builds the schema."""
    _upgraded(conn, migration_module)

    columns = {c["name"] for c in inspect(conn).get_columns("docs_pages")}

    assert "search" not in columns
    assert {"path", "title", "section", "summary", "body"} <= columns


def test_the_search_vector_is_weighted_by_field(migration_module):
    """Title beats summary beats body. Without the weights, a page that mentions
    a term in passing competes with the page named after it."""
    ddl = migration_module._SEARCH_COLUMN

    assert "setweight(to_tsvector('english', coalesce(title, '')), 'A')" in ddl
    assert "setweight(to_tsvector('english', coalesce(summary, '')), 'B')" in ddl
    assert "setweight(to_tsvector('english', coalesce(body, '')), 'C')" in ddl
    assert "GENERATED ALWAYS AS" in ddl and "STORED" in ddl


def test_downgrade_removes_both_tables(conn, migration_module):
    _upgraded(conn, migration_module)
    conn.execute(
        text("INSERT INTO docs_pages (path, title, section, body) VALUES ('a','A','C','b')")
    )

    migration_module.downgrade()

    tables = set(inspect(conn).get_table_names())
    assert "docs_pages" not in tables
    assert "docs_corpus_meta" not in tables
