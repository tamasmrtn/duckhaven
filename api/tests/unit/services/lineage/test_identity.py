"""Telling a renamed table from a new one wearing its name.

The two cases look identical from the outside and demand opposite handling, so
every test here is really about one question: does the Iceberg id, or the name,
decide what happens to the lineage?
"""

from __future__ import annotations

import sqlalchemy as sa

from api.models.lineage import LineageEdge
from api.models.table_metadata import TableMetadata
from api.services.lineage.identity import reconcile_table_identity
from api.services.lineage.ingest import CanonicalEdge, upsert_edges
from api.services.lineage.keys import internal_ref

UUID_A = "11111111-aaaa-4aaa-8aaa-111111111111"
UUID_B = "22222222-bbbb-4bbb-8bbb-222222222222"


async def _edges(db) -> list[LineageEdge]:
    return list((await db.execute(sa.select(LineageEdge))).scalars().all())


async def _meta(env, catalog: str, schema: str, table: str, table_uuid: str | None):
    row = TableMetadata(
        catalog_id=env["catalogs"][catalog].id,
        schema_name=schema,
        table_name=table,
        table_uuid=table_uuid,
    )
    env["db"].add(row)
    await env["db"].flush()
    return row


async def _lineage_into(env, target: str, *, catalog: str = "warehouse", provider="execution"):
    """`raw.analytics.src -> <catalog>.analytics.<target>`."""
    return await upsert_edges(
        env["db"],
        [
            CanonicalEdge(
                source=internal_ref(env["catalogs"]["raw"].id, "analytics", "src"),
                target=internal_ref(env["catalogs"][catalog].id, "analytics", target),
                operation="create_table_as",
            )
        ],
        provider=provider,
    )


async def _reconcile(env, table: str, table_uuid: str | None, *, catalog: str = "warehouse"):
    return await reconcile_table_identity(
        env["db"],
        catalog_id=env["catalogs"][catalog].id,
        schema="analytics",
        table=table,
        table_uuid=table_uuid,
    )


# --- recording --------------------------------------------------------------


async def test_a_first_sighting_records_the_identity(graph_env):
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", None)

    assert await _reconcile(graph_env, "dim", UUID_A) == "recorded"

    row = (
        await db.execute(sa.select(TableMetadata).where(TableMetadata.table_name == "dim"))
    ).scalar_one()
    assert row.table_uuid == UUID_A


async def test_a_table_with_no_sidecar_yet_gets_one(graph_env):
    """A table DuckHaven never created or wrote to still needs its identity on
    file, or the first rename after that point is undetectable."""
    db = graph_env["db"]
    assert await _reconcile(graph_env, "external_dim", UUID_A) == "recorded"

    row = (
        await db.execute(sa.select(TableMetadata).where(TableMetadata.table_name == "external_dim"))
    ).scalar_one()
    assert row.table_uuid == UUID_A


async def test_a_second_look_at_an_unchanged_table_concludes_nothing(graph_env):
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    assert await _reconcile(graph_env, "dim", UUID_A) == "unchanged"


async def test_no_identity_reported_is_a_no_op(graph_env):
    """Polaris does not always carry one. Guessing from the name alone is
    precisely what this module exists to avoid."""
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    await _lineage_into(graph_env, "dim")

    assert await _reconcile(graph_env, "dim", None) is None
    assert len(await _edges(db)) == 1


# --- rename -----------------------------------------------------------------


async def test_a_rename_carries_the_lineage_across(graph_env):
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    await _lineage_into(graph_env, "dim")
    (before,) = await _edges(db)
    first_seen, counted = before.first_seen_at, before.observation_count

    assert await _reconcile(graph_env, "dim_v2", UUID_A) == "renamed"

    (after,) = await _edges(db)
    assert after.target_table == "dim_v2"
    assert (
        after.target_key
        == internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim_v2").key
    )
    # It is the same relationship, so it keeps the history it accumulated.
    assert (after.first_seen_at, after.observation_count) == (first_seen, counted)


async def test_a_rename_moves_edges_where_the_table_is_the_source(graph_env):
    db = graph_env["db"]
    await _meta(graph_env, "raw", "analytics", "src", UUID_A)
    await _lineage_into(graph_env, "dim")

    assert await _reconcile(graph_env, "src_v2", UUID_A, catalog="raw") == "renamed"

    (edge,) = await _edges(db)
    assert edge.source_table == "src_v2"
    assert edge.target_table == "dim"


async def test_the_sidecar_follows_the_table(graph_env):
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)

    await _reconcile(graph_env, "dim_v2", UUID_A)

    rows = list((await db.execute(sa.select(TableMetadata))).scalars().all())
    assert [(r.table_name, r.table_uuid) for r in rows] == [("dim_v2", UUID_A)]


async def test_a_rename_onto_an_occupied_name_merges_rather_than_raising(graph_env):
    """Both names had lineage from the same producer to the same counterpart.
    They are one relationship seen under two names, so the widest window and the
    summed count survive — and, critically, the unique constraint is not hit."""
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    await _meta(graph_env, "warehouse", "analytics", "dim_v2", None)
    await _lineage_into(graph_env, "dim")
    await _lineage_into(graph_env, "dim_v2")
    await _lineage_into(graph_env, "dim_v2")  # two observations on the newer name

    assert await _reconcile(graph_env, "dim_v2", UUID_A) == "renamed"

    edges = await _edges(db)
    assert len(edges) == 1
    assert edges[0].target_table == "dim_v2"
    assert edges[0].observation_count == 3


async def test_a_rename_onto_a_name_the_table_was_built_from_drops_the_self_edge(graph_env):
    """`raw.src -> raw.staging`, then `staging` is renamed to `src`. The edge
    would point at itself, which carries no information — the same rule
    extraction applies."""
    db = graph_env["db"]
    await _meta(graph_env, "raw", "analytics", "staging", UUID_A)
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(graph_env["catalogs"]["raw"].id, "analytics", "src"),
                target=internal_ref(graph_env["catalogs"]["raw"].id, "analytics", "staging"),
            )
        ],
        provider="execution",
    )

    assert await _reconcile(graph_env, "src", UUID_A, catalog="raw") == "renamed"

    assert await _edges(db) == []


# --- drop and recreate ------------------------------------------------------


async def test_a_recreated_table_does_not_inherit_the_old_ones_lineage(graph_env):
    """The correctness requirement this whole module exists for. Carrying a
    dropped table's lineage onto an unrelated new table with the same name would
    have the graph assert relationships that never existed."""
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    await _lineage_into(graph_env, "dim")

    assert await _reconcile(graph_env, "dim", UUID_B) == "recreated"

    assert await _edges(db) == []
    row = (
        await db.execute(sa.select(TableMetadata).where(TableMetadata.table_name == "dim"))
    ).scalar_one()
    assert row.table_uuid == UUID_B


async def test_a_recreated_table_only_loses_its_own_lineage(graph_env):
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    await _lineage_into(graph_env, "dim")
    await _lineage_into(graph_env, "rollup")

    await _reconcile(graph_env, "dim", UUID_B)

    assert {e.target_table for e in await _edges(db)} == {"rollup"}


# --- collisions -------------------------------------------------------------


async def test_the_same_name_in_two_catalogs_stays_independent(graph_env):
    """Identity is looked up within a catalog and lineage keys embed the catalog,
    so two catalogs holding a `dim` are two assets, never one."""
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    await _meta(graph_env, "raw", "analytics", "dim", UUID_B)
    await _lineage_into(graph_env, "dim", catalog="warehouse")
    await _lineage_into(graph_env, "dim", catalog="raw")

    # `warehouse.dim` is renamed. `raw.dim` must not notice.
    assert await _reconcile(graph_env, "dim_v2", UUID_A) == "renamed"

    targets = {(e.target_catalog_id, e.target_table) for e in await _edges(db)}
    assert targets == {
        (graph_env["catalogs"]["warehouse"].id, "dim_v2"),
        (graph_env["catalogs"]["raw"].id, "dim"),
    }


async def test_an_identity_recorded_in_another_catalog_is_not_a_rename(graph_env):
    """Iceberg ids are unique in practice, but the lookup is scoped to a catalog
    anyway — a coincidence across catalogs must not move anything."""
    db = graph_env["db"]
    await _meta(graph_env, "raw", "analytics", "src", UUID_A)
    await _lineage_into(graph_env, "dim")

    assert await _reconcile(graph_env, "dim", UUID_A, catalog="warehouse") == "recorded"

    (edge,) = await _edges(db)
    assert (edge.source_table, edge.target_table) == ("src", "dim")


async def test_imported_lineage_moves_with_a_rename_too(graph_env):
    """Rekeying is per-address, not per-provider: a renamed table takes every
    producer's claims about it along."""
    db = graph_env["db"]
    await _meta(graph_env, "warehouse", "analytics", "dim", UUID_A)
    await _lineage_into(graph_env, "dim", provider="execution")
    await _lineage_into(graph_env, "dim", provider="dbt")

    await _reconcile(graph_env, "dim_v2", UUID_A)

    edges = await _edges(db)
    assert {e.provider for e in edges} == {"execution", "dbt"}
    assert {e.target_table for e in edges} == {"dim_v2"}
