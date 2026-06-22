"""Migration 0010 backfills one default catalog per workspace.

The full chain can't run on SQLite (0001 uses postgresql.JSONB), so this seeds a
minimal schema and exercises the real ``_backfill``: every workspace gets one
catalog (Polaris name = workspace slug, so no Polaris rename), a default binding,
and its metadata re-pointed at the new catalog.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0010_multi_catalog.py"


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0010", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded_conn():
    engine = create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(text("CREATE TABLE users (id text)"))
    conn.execute(text("CREATE TABLE storage_backends (id text)"))
    conn.execute(
        text("CREATE TABLE workspaces (id text, slug text, name text, storage_backend_id text)")
    )
    conn.execute(
        text("CREATE TABLE workspace_members (workspace_id text, user_id text, role text)")
    )
    conn.execute(
        text(
            "CREATE TABLE catalogs (id text, slug text, name text, polaris_name text, "
            "storage_backend_id text, created_by text, created_at text)"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE workspace_catalogs (workspace_id text, catalog_id text, "
            "is_default integer, attached_at text, attached_by text)"
        )
    )
    conn.execute(text("CREATE TABLE table_metadata (workspace_id text, catalog_id text)"))
    conn.execute(text("CREATE TABLE table_health_sample (workspace_id text, catalog_id text)"))
    conn.execute(
        text("CREATE TABLE maintenance_recommendation (workspace_id text, catalog_id text)")
    )

    user_id, backend_id = uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute(text("INSERT INTO users VALUES (:i)"), {"i": user_id})
    conn.execute(text("INSERT INTO storage_backends VALUES (:i)"), {"i": backend_id})
    # Two workspaces; one slug has a hyphen (must sanitize for the catalog slug).
    for slug, name in [("acme-analytics", "Acme"), ("research", "Research")]:
        ws_id = uuid.uuid4().hex
        conn.execute(
            text("INSERT INTO workspaces VALUES (:i, :s, :n, :b)"),
            {"i": ws_id, "s": slug, "n": name, "b": backend_id},
        )
        conn.execute(
            text("INSERT INTO workspace_members VALUES (:w, :u, 'owner')"),
            {"w": ws_id, "u": user_id},
        )
        conn.execute(text("INSERT INTO table_metadata VALUES (:w, NULL)"), {"w": ws_id})
    yield conn
    conn.close()


def test_0010_backfills_one_catalog_per_workspace(migration_module, seeded_conn):
    migration_module._backfill(seeded_conn)

    cats = seeded_conn.execute(
        text("SELECT slug, polaris_name, storage_backend_id FROM catalogs ORDER BY polaris_name")
    ).fetchall()
    # One catalog per workspace; Polaris name preserved as the workspace slug
    # (no rename), and the catalog slug is sanitized to be identifier-safe.
    assert [(c[0], c[1]) for c in cats] == [
        ("acme_analytics", "acme-analytics"),
        ("research", "research"),
    ]
    assert all(c[2] is not None for c in cats)  # backend carried over

    # Exactly one default binding per workspace.
    bindings = seeded_conn.execute(
        text("SELECT COUNT(*) FROM workspace_catalogs WHERE is_default = 1")
    ).scalar()
    assert bindings == 2

    # Every table_metadata row was re-pointed at a catalog.
    unmapped = seeded_conn.execute(
        text("SELECT COUNT(*) FROM table_metadata WHERE catalog_id IS NULL")
    ).scalar()
    assert unmapped == 0
