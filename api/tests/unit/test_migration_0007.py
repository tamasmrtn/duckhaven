"""Migration 0007 collapses the legacy local_fs/nas kinds into object_store.

The full migration chain can't run on SQLite (0001 uses postgresql.JSONB), so
this binds Alembic's `op` to a minimal storage_backends table and exercises the
real 0007 upgrade/downgrade against seeded rows.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0007_merge_object_store.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0007", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded_conn():
    engine = create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(
        text("CREATE TABLE storage_backends (id text, kind text, name text, root_uri text)")
    )
    for kind, root_uri in [("local_fs", "alpha"), ("nas", "beta"), ("s3", "s3://ext/x")]:
        conn.execute(
            text("INSERT INTO storage_backends VALUES (:id, :kind, :name, :uri)"),
            {"id": uuid.uuid4().hex, "kind": kind, "name": kind, "uri": root_uri},
        )
    yield conn
    conn.close()


def _rows(conn) -> list[tuple[str, str]]:
    return sorted(conn.execute(text("SELECT kind, root_uri FROM storage_backends")).fetchall())


def _run(conn, fn) -> None:
    op = Operations(MigrationContext.configure(conn))
    with Operations.context(op):
        fn()


def test_0007_merges_local_fs_and_nas(migration_module, seeded_conn):
    _run(seeded_conn, migration_module.upgrade)
    # Both legacy kinds collapse to object_store; root_uri is left untouched so
    # each workspace's Polaris base location stays stable. External s3 is intact.
    assert _rows(seeded_conn) == [
        ("object_store", "alpha"),
        ("object_store", "beta"),
        ("s3", "s3://ext/x"),
    ]

    _run(seeded_conn, migration_module.downgrade)
    # Downgrade is lossy: the meaningless local_fs/nas split can't be recovered.
    assert _rows(seeded_conn) == [
        ("local_fs", "alpha"),
        ("local_fs", "beta"),
        ("s3", "s3://ext/x"),
    ]
