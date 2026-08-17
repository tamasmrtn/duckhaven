"""Migration 0036 creates the semantic layer's five tables.

Driven against a bare SQLite connection through an Alembic operations context,
like ``test_migration_0035``.

Most of what is asserted here is shape, but two constraints are pinned because
they are the migration's actual safety claims rather than bookkeeping.
``ck_semantic_relationships_cardinality`` is what makes fan-out unrepresentable:
if ``one_to_many`` ever became insertable, every metric crossing that join would
silently over-count. And ``semantic_metrics.time_dimension_id`` must be
``SET NULL`` rather than ``CASCADE`` — deleting a time dimension has to leave the
metric standing and visibly incomplete, because a metric that vanishes when its
axis is removed takes its own definition down with it and nobody finds out.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

SEMANTIC_TABLES = {
    "semantic_models",
    "semantic_datasets",
    "semantic_dimensions",
    "semantic_metrics",
    "semantic_relationships",
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_module():
    return _load("migration_0036", VERSIONS / "0036_semantic_layer.py")


def _prerequisites(conn):
    conn.execute(text("CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE catalogs (id CHAR(32) PRIMARY KEY)"))
    conn.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))


def _rebind(module, conn):
    module.op = Operations(MigrationContext.configure(conn))


def _upgraded(conn, migration_module):
    _prerequisites(conn)
    _rebind(migration_module, conn)
    migration_module.upgrade()


def _seed_model(conn, *, model_id="m1"):
    conn.execute(text("INSERT OR IGNORE INTO workspaces (id) VALUES ('w1')"))
    conn.execute(text("INSERT OR IGNORE INTO catalogs (id) VALUES ('c1')"))
    conn.execute(
        text(
            "INSERT INTO semantic_models (id, workspace_id, slug, name) "
            f"VALUES ('{model_id}', 'w1', 'sales', 'Sales')"
        )
    )


def _seed_dataset(conn, *, dataset_id="d1", model_id="m1", name="orders"):
    conn.execute(
        text(
            "INSERT INTO semantic_datasets "
            "  (id, model_id, name, catalog_id, schema_name, table_name) "
            f"VALUES ('{dataset_id}', '{model_id}', '{name}', 'c1', 'analytics', '{name}')"
        )
    )


def test_revision_links_to_0035(migration_module):
    assert migration_module.revision == "0036"
    assert migration_module.down_revision == "0035"


def test_upgrade_creates_the_five_tables(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)

        assert SEMANTIC_TABLES <= set(inspect(conn).get_table_names())


def test_model_slug_is_unique_per_workspace(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)

        uniques = {
            u["name"]: u["column_names"]
            for u in inspect(conn).get_unique_constraints("semantic_models")
        }
        assert uniques["uq_semantic_models_slug"] == ["workspace_id", "slug"]


def test_a_new_model_starts_as_a_native_draft(migration_module):
    """Defaults matter here: a model must never arrive already trusted.

    ``draft`` keeps it away from the assistant until an owner publishes it, and
    ``native`` marks it as something a person wrote rather than something an
    import asserted.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)
        _seed_model(conn)

        status, provider = conn.execute(text("SELECT status, provider FROM semantic_models")).one()
        assert status == "draft"
        assert provider == "native"


def test_bindings_start_unchecked_rather_than_ok(migration_module):
    """ "unchecked" is not a synonym for "ok" — nothing has looked yet."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)
        _seed_model(conn)
        _seed_dataset(conn)

        state = conn.execute(text("SELECT validation_state FROM semantic_datasets")).scalar_one()
        assert state == "unchecked"


def test_fan_out_cardinality_cannot_be_stored(migration_module):
    """The load-bearing constraint: ``one_to_many`` has no representation.

    A join toward a non-unique side multiplies fact rows, so every SUM crossing it
    comes back inflated with no error. This is enforced in the schema rather than
    only in validation so no import path, backfill or hand-written UPDATE can
    introduce one.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)
        _seed_model(conn)
        _seed_dataset(conn, dataset_id="d1", name="orders")
        _seed_dataset(conn, dataset_id="d2", name="customers")

        def insert(cardinality: str, rel_id: str):
            conn.execute(
                text(
                    "INSERT INTO semantic_relationships "
                    "  (id, model_id, name, left_dataset_id, right_dataset_id,"
                    "   join_columns, cardinality) "
                    f"VALUES ('{rel_id}', 'm1', '{rel_id}', 'd1', 'd2', '[]', '{cardinality}')"
                )
            )

        insert("many_to_one", "r1")  # allowed
        with pytest.raises(Exception, match="ck_semantic_relationships_cardinality|CHECK"):
            insert("one_to_many", "r2")


def test_dimension_kind_is_constrained(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)
        _seed_model(conn)
        _seed_dataset(conn)

        with pytest.raises(Exception, match="ck_semantic_dimensions_kind|CHECK"):
            conn.execute(
                text(
                    "INSERT INTO semantic_dimensions (id, model_id, dataset_id, name, expr, kind) "
                    "VALUES ('x1', 'm1', 'd1', 'weird', 'col', 'ordinal')"
                )
            )


def test_metric_agg_is_constrained(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)
        _seed_model(conn)
        _seed_dataset(conn)

        with pytest.raises(Exception, match="ck_semantic_metrics_agg|CHECK"):
            conn.execute(
                text(
                    "INSERT INTO semantic_metrics (id, model_id, dataset_id, name, agg) "
                    "VALUES ('x1', 'm1', 'd1', 'revenue', 'median')"
                )
            )


def test_dropping_a_time_dimension_leaves_the_metric_standing(migration_module):
    """SET NULL, not CASCADE.

    A metric whose time axis was deleted is broken and needs fixing. A metric that
    disappears along with its axis is a definition silently lost.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        _upgraded(conn, migration_module)
        _seed_model(conn)
        _seed_dataset(conn)
        conn.execute(
            text(
                "INSERT INTO semantic_dimensions "
                "  (id, model_id, dataset_id, name, expr, kind) "
                "VALUES ('t1', 'm1', 'd1', 'order_date', 'order_date', 'time')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO semantic_metrics "
                "  (id, model_id, dataset_id, name, agg, expr, time_dimension_id) "
                "VALUES ('me1', 'm1', 'd1', 'revenue', 'sum', 'total_amount', 't1')"
            )
        )

        conn.execute(text("DELETE FROM semantic_dimensions WHERE id = 't1'"))

        name, axis = conn.execute(
            text("SELECT name, time_dimension_id FROM semantic_metrics")
        ).one()
        assert name == "revenue"
        assert axis is None


def test_deleting_a_model_takes_its_children(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        _upgraded(conn, migration_module)
        _seed_model(conn)
        _seed_dataset(conn)

        conn.execute(text("DELETE FROM semantic_models WHERE id = 'm1'"))

        assert conn.execute(text("SELECT count(*) FROM semantic_datasets")).scalar_one() == 0


def test_the_reverse_index_exists(migration_module):
    """ "Which metrics use this table?" must be an index lookup, not a scan."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)

        indexes = {
            i["name"]: i["column_names"] for i in inspect(conn).get_indexes("semantic_datasets")
        }
        assert indexes["ix_semantic_datasets_binding"] == [
            "catalog_id",
            "schema_name",
            "table_name",
        ]


def test_the_migration_and_the_models_agree(migration_module):
    """The drift check.

    Unit tests build their schema from ``Base.metadata``; deployments build it
    from this migration. Nothing else compares the two, so a column added to a
    model and not to the migration would pass every test and then fail on a real
    database — the one place it cannot be caught cheaply.
    """
    import api.models  # noqa: F401 — registers every model
    from api.db.base import Base

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)
        inspector = inspect(conn)

        for table in sorted(SEMANTIC_TABLES):
            migrated = {c["name"] for c in inspector.get_columns(table)}
            declared = {c.name for c in Base.metadata.tables[table].columns}
            assert migrated == declared, f"{table} drifted"


def test_downgrade_removes_every_table_it_created(migration_module):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _upgraded(conn, migration_module)
        _rebind(migration_module, conn)
        migration_module.downgrade()

        remaining = set(inspect(conn).get_table_names())
        assert not (SEMANTIC_TABLES & remaining)
        # The tables it depends on are untouched.
        assert {"workspaces", "catalogs", "users"} <= remaining
