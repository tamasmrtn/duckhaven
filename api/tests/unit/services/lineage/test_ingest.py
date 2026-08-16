"""Persisting edges: identity, idempotence, provider coexistence, cleanup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.models.lineage import LineageColumnEdge, LineageEdge
from api.models.query import Query
from api.services.lineage.columns import ColumnPair
from api.services.lineage.ingest import (
    CanonicalEdge,
    delete_schema_lineage,
    delete_table_lineage,
    purge_provider,
    reconcile_provider_run,
    record_execution_lineage,
    rekey_table_lineage,
    upsert_edges,
)
from api.services.lineage.keys import external_ref, internal_ref


async def _edges(db) -> list[LineageEdge]:
    rows = await db.execute(sa.select(LineageEdge))
    return list(rows.scalars().all())


def _edge(env, source_table="src", target_table="dim", *, source_catalog="raw"):
    return CanonicalEdge(
        source=internal_ref(env["catalogs"][source_catalog].id, "analytics", source_table),
        target=internal_ref(env["catalogs"]["warehouse"].id, "analytics", target_table),
        operation="create_table_as",
    )


# --- upsert -----------------------------------------------------------------


async def test_first_observation_creates_the_edge(graph_env):
    db = graph_env["db"]
    result = await upsert_edges(db, [_edge(graph_env)], provider="execution")
    assert (result.created, result.updated) == (1, 0)

    (edge,) = await _edges(db)
    assert edge.source_table == "src"
    assert edge.target_table == "dim"
    assert edge.provider == "execution"
    assert edge.observation_count == 1
    assert edge.confidence == "exact"


async def test_reobservation_updates_in_place(graph_env):
    db = graph_env["db"]
    first = datetime.now(tz=UTC) - timedelta(days=1)
    await upsert_edges(db, [_edge(graph_env)], provider="execution", observed_at=first)
    result = await upsert_edges(db, [_edge(graph_env)], provider="execution")

    assert (result.created, result.updated) == (0, 1)
    (edge,) = await _edges(db)
    assert edge.observation_count == 2
    # first_seen_at is the anchor; only last_seen_at moves.
    assert edge.first_seen_at.replace(tzinfo=UTC) == first
    assert edge.last_seen_at.replace(tzinfo=UTC) > first


async def test_two_providers_asserting_the_same_pair_coexist(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_edge(graph_env)], provider="execution")
    await upsert_edges(db, [_edge(graph_env)], provider="dbt", provider_run_id="run-1")

    edges = await _edges(db)
    assert len(edges) == 2
    assert {e.provider for e in edges} == {"execution", "dbt"}


async def test_external_source_is_stored_without_a_catalog(graph_env):
    db = graph_env["db"]
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=external_ref("crm_pg", "public", "customers"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
                operation="model",
            )
        ],
        provider="dbt",
    )
    (edge,) = await _edges(db)
    assert edge.source_catalog_id is None
    assert edge.source_system == "crm_pg"
    assert edge.source_key.startswith("ext:")


async def test_empty_edge_list_is_a_no_op(graph_env):
    result = await upsert_edges(graph_env["db"], [], provider="execution")
    assert (result.created, result.updated) == (0, 0)


# --- reconciliation ---------------------------------------------------------


async def test_reconcile_removes_targets_the_run_did_not_reassert(graph_env):
    db = graph_env["db"]
    stale = _edge(graph_env, "old_src", "dim")
    await upsert_edges(db, [stale], provider="dbt", provider_run_id="run-1")

    fresh = _edge(graph_env, "new_src", "dim")
    await upsert_edges(db, [fresh], provider="dbt", provider_run_id="run-2")
    removed = await reconcile_provider_run(
        db, provider="dbt", provider_run_id="run-2", target_keys={fresh.target.key}
    )

    assert removed == 1
    assert [e.source_table for e in await _edges(db)] == ["new_src"]


async def test_partial_run_leaves_untouched_targets_alone(graph_env):
    # The case that makes reconciliation safe: `dbt run --select one_model` must
    # not delete lineage for models it never built.
    db = graph_env["db"]
    other = _edge(graph_env, "src_b", "dim_b")
    await upsert_edges(db, [other], provider="dbt", provider_run_id="run-1")

    rebuilt = _edge(graph_env, "src_a2", "dim_a")
    await upsert_edges(db, [rebuilt], provider="dbt", provider_run_id="run-2")
    await reconcile_provider_run(
        db, provider="dbt", provider_run_id="run-2", target_keys={rebuilt.target.key}
    )

    assert {e.target_table for e in await _edges(db)} == {"dim_a", "dim_b"}


async def test_reconcile_never_touches_another_providers_edges(graph_env):
    db = graph_env["db"]
    edge = _edge(graph_env)
    await upsert_edges(db, [edge], provider="execution")
    await upsert_edges(db, [edge], provider="dbt", provider_run_id="run-1")

    await reconcile_provider_run(
        db, provider="dbt", provider_run_id="run-2", target_keys={edge.target.key}
    )

    remaining = await _edges(db)
    assert [e.provider for e in remaining] == ["execution"]


async def test_reconcile_with_no_targets_deletes_nothing(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_edge(graph_env)], provider="dbt", provider_run_id="run-1")
    assert (
        await reconcile_provider_run(db, provider="dbt", provider_run_id="run-2", target_keys=set())
        == 0
    )
    assert len(await _edges(db)) == 1


# --- purge ------------------------------------------------------------------


async def test_purge_provider_removes_only_that_provider(graph_env):
    db = graph_env["db"]
    edge = _edge(graph_env)
    await upsert_edges(db, [edge], provider="execution")
    await upsert_edges(db, [edge], provider="dbt", provider_run_id="run-1")

    assert await purge_provider(db, provider="dbt") == 1
    assert [e.provider for e in await _edges(db)] == ["execution"]


async def test_execution_lineage_cannot_be_purged_by_provider(graph_env):
    with pytest.raises(ValueError):
        await purge_provider(graph_env["db"], provider="execution")


# --- drop cleanup -----------------------------------------------------------


async def test_dropping_a_table_removes_edges_on_both_sides(graph_env):
    db = graph_env["db"]
    warehouse = graph_env["catalogs"]["warehouse"]
    await upsert_edges(
        db,
        [
            _edge(graph_env, "src", "dim"),  # dim is a target
            CanonicalEdge(  # dim is a source
                source=internal_ref(warehouse.id, "analytics", "dim"),
                target=internal_ref(warehouse.id, "analytics", "downstream"),
                operation="create_table_as",
            ),
        ],
        provider="execution",
    )
    await delete_table_lineage(db, warehouse.id, "analytics", "dim")
    assert await _edges(db) == []


async def test_dropping_a_schema_removes_every_edge_beneath_it(graph_env):
    db = graph_env["db"]
    warehouse = graph_env["catalogs"]["warehouse"]
    await upsert_edges(
        db,
        [
            _edge(graph_env, "src", "dim"),
            CanonicalEdge(
                source=internal_ref(warehouse.id, "other", "a"),
                target=internal_ref(warehouse.id, "other", "b"),
            ),
        ],
        provider="execution",
    )
    await delete_schema_lineage(db, warehouse.id, "analytics")
    assert {e.target_schema for e in await _edges(db)} == {"other"}


# --- the native path --------------------------------------------------------


async def _query(env, sql: str, active_catalog: str | None = "warehouse") -> Query:
    query = Query(
        workspace_id=env["workspace"].id,
        user_id=env["user"].id,
        sql=sql,
        status="done",
        active_catalog=active_catalog,
    )
    env["db"].add(query)
    await env["db"].flush()
    return query


async def test_record_execution_lineage_persists_the_derived_edge(graph_env):
    db = graph_env["db"]
    query = await _query(
        graph_env, "CREATE TABLE warehouse.analytics.dim AS SELECT * FROM raw.analytics.src"
    )
    result = await record_execution_lineage(db, query)

    assert result.created == 1
    (edge,) = await _edges(db)
    assert edge.provider == "execution"
    assert edge.operation == "create_table_as"
    assert edge.last_query_id == query.id
    assert edge.workspace_id == graph_env["workspace"].id


async def test_unqualified_names_resolve_when_the_caller_sent_no_catalog(graph_env):
    """`queries.active_catalog` is NULL whenever the request omitted it — every
    scheduled run, and any client that does not send one. The agent still
    resolved those names against the workspace default, so lineage must too, or
    the most ordinary statement there is records nothing."""
    db = graph_env["db"]
    query = await _query(graph_env, "CREATE TABLE dim AS SELECT * FROM src", active_catalog=None)

    result = await record_execution_lineage(db, query)

    assert result.created == 1
    (edge,) = await _edges(db)
    # `warehouse` is the workspace's default catalog.
    assert edge.source_catalog_id == graph_env["catalogs"]["warehouse"].id
    assert (edge.source_table, edge.target_table) == ("src", "dim")


async def test_record_execution_lineage_ignores_a_read(graph_env):
    db = graph_env["db"]
    query = await _query(graph_env, "SELECT * FROM raw.analytics.src")
    assert (await record_execution_lineage(db, query)).created == 0
    assert await _edges(db) == []


@pytest.mark.parametrize("origin", ["sample", "metadata"])
async def test_duckhavens_own_synthetic_queries_are_skipped(graph_env, origin):
    # These run on every catalog click. They are reads, so they would record
    # nothing anyway — this just keeps the parse off that path.
    db = graph_env["db"]
    query = await _query(
        graph_env, "CREATE TABLE warehouse.analytics.dim AS SELECT * FROM raw.analytics.src"
    )
    query.origin = origin
    await db.flush()

    assert (await record_execution_lineage(db, query)).created == 0
    assert await _edges(db) == []


async def test_record_execution_lineage_swallows_a_parse_failure(graph_env):
    db = graph_env["db"]
    query = await _query(graph_env, "selct * from foo")
    # Fails open: no edges, no exception, the query itself is untouched.
    assert (await record_execution_lineage(db, query)).created == 0
    assert await _edges(db) == []


async def test_repeated_runs_of_the_same_statement_converge_on_one_edge(graph_env):
    db = graph_env["db"]
    sql = "CREATE TABLE warehouse.analytics.dim AS SELECT * FROM raw.analytics.src"
    for _ in range(3):
        await record_execution_lineage(db, await _query(graph_env, sql))

    (edge,) = await _edges(db)
    assert edge.observation_count == 3


# --- observation time -------------------------------------------------------


async def test_an_older_observation_widens_the_window_without_moving_last_seen(graph_env):
    """The property that makes replaying history safe. A statement from March,
    discovered today, extends when the relationship started — it does not claim
    the relationship was confirmed today."""
    db = graph_env["db"]
    recent = datetime.now(tz=UTC) - timedelta(days=1)
    old = datetime.now(tz=UTC) - timedelta(days=200)

    await upsert_edges(db, [_edge(graph_env)], provider="execution", observed_at=recent)
    await upsert_edges(db, [_edge(graph_env)], provider="execution", observed_at=old)

    (edge,) = await _edges(db)
    assert edge.first_seen_at.replace(tzinfo=UTC) == old
    assert edge.last_seen_at.replace(tzinfo=UTC) == recent
    assert edge.observation_count == 2


async def test_an_older_observation_does_not_steal_the_query_link(graph_env):
    """`last_query_id` means "the query that most recently produced this". An
    older statement arriving later must not take that over, or the click-through
    would open the wrong SQL."""
    db = graph_env["db"]
    newer, older = uuid.uuid4(), uuid.uuid4()
    await upsert_edges(
        db,
        [_edge(graph_env)],
        provider="execution",
        observed_at=datetime.now(tz=UTC) - timedelta(days=1),
        last_query_id=newer,
    )
    await upsert_edges(
        db,
        [_edge(graph_env)],
        provider="execution",
        observed_at=datetime.now(tz=UTC) - timedelta(days=200),
        last_query_id=older,
    )

    (edge,) = await _edges(db)
    assert edge.last_query_id == newer


async def test_observations_land_the_same_way_in_either_order(graph_env):
    db = graph_env["db"]
    a = datetime.now(tz=UTC) - timedelta(days=90)
    b = datetime.now(tz=UTC) - timedelta(days=5)

    for stamps in ((a, b), (b, a)):
        await db.execute(sa.delete(LineageEdge))
        await db.flush()
        for stamp in stamps:
            await upsert_edges(db, [_edge(graph_env)], provider="execution", observed_at=stamp)
        (edge,) = await _edges(db)
        assert (
            edge.first_seen_at.replace(tzinfo=UTC),
            edge.last_seen_at.replace(tzinfo=UTC),
            edge.observation_count,
        ) == (a, b, 2)


async def test_execution_lineage_is_stamped_with_when_the_statement_ran(graph_env):
    db = graph_env["db"]
    query = await _query(
        graph_env, "CREATE TABLE warehouse.analytics.dim AS SELECT * FROM raw.analytics.src"
    )
    ran_at = datetime.now(tz=UTC) - timedelta(days=45)
    query.started_at = ran_at
    query.finished_at = ran_at
    await db.flush()

    await record_execution_lineage(db, query)

    (edge,) = await _edges(db)
    assert edge.last_seen_at.replace(tzinfo=UTC) == ran_at


# --- rekey ------------------------------------------------------------------


async def test_rekey_moves_an_edge_and_keeps_its_history(graph_env):
    db = graph_env["db"]
    catalog_id = graph_env["catalogs"]["warehouse"].id
    await upsert_edges(db, [_edge(graph_env)], provider="execution")
    await upsert_edges(db, [_edge(graph_env)], provider="execution")

    moved = await rekey_table_lineage(
        db,
        catalog_id,
        old_schema="analytics",
        old_table="dim",
        new_schema="analytics",
        new_table="dim_v2",
    )

    assert moved == 1
    (edge,) = await _edges(db)
    assert edge.target_table == "dim_v2"
    assert edge.target_key == internal_ref(catalog_id, "analytics", "dim_v2").key
    assert edge.observation_count == 2


async def test_rekey_to_the_same_address_is_a_no_op(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_edge(graph_env)], provider="execution")

    moved = await rekey_table_lineage(
        db,
        graph_env["catalogs"]["warehouse"].id,
        old_schema="analytics",
        old_table="dim",
        new_schema="analytics",
        new_table="dim",
    )

    assert moved == 0
    assert len(await _edges(db)) == 1


async def test_rekey_across_schemas_moves_the_edge(graph_env):
    db = graph_env["db"]
    catalog_id = graph_env["catalogs"]["warehouse"].id
    await upsert_edges(db, [_edge(graph_env)], provider="execution")

    await rekey_table_lineage(
        db,
        catalog_id,
        old_schema="analytics",
        old_table="dim",
        new_schema="marts",
        new_table="dim",
    )

    (edge,) = await _edges(db)
    assert (edge.target_schema, edge.target_table) == ("marts", "dim")


# --- column-level detail ----------------------------------------------------


async def _columns(db, edge_id=None) -> list[LineageColumnEdge]:
    stmt = sa.select(LineageColumnEdge)
    if edge_id is not None:
        stmt = stmt.where(LineageColumnEdge.edge_id == edge_id)
    rows = await db.execute(stmt)
    return list(rows.scalars().all())


def _with_columns(env, *pairs, state="derived", **kwargs):
    base = _edge(env, **kwargs)
    return CanonicalEdge(
        source=base.source,
        target=base.target,
        operation=base.operation,
        column_lineage=state,
        columns=tuple(ColumnPair(source_column=s, target_column=t) for s, t in pairs),
    )


async def test_an_edge_starts_with_no_column_detail(graph_env):
    """The default has to be `unknown`, not `derived` with nothing in it.

    Otherwise every producer that never looked would be claiming it had, and an
    empty column list would stop meaning anything.
    """
    db = graph_env["db"]
    await upsert_edges(db, [_edge(graph_env)], provider="execution")

    (edge,) = await _edges(db)
    assert edge.column_lineage == "unknown"
    assert await _columns(db) == []


async def test_column_pairs_are_written_against_their_edge(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"), ("b", "x"))], provider="execution")

    (edge,) = await _edges(db)
    assert edge.column_lineage == "derived"
    assert {(c.source_column, c.target_column) for c in await _columns(db, edge.id)} == {
        ("a", "x"),
        ("b", "x"),
    }


async def test_derived_with_no_pairs_is_recorded_as_an_answer(graph_env):
    """The filter-only case, and the reason the state column exists at all."""
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env)], provider="execution")

    (edge,) = await _edges(db)
    assert edge.column_lineage == "derived"
    assert await _columns(db) == []


async def test_reobserving_accumulates_rather_than_replacing(graph_env):
    """Two statements can move different columns along the same relationship.

    Replacing would make each run retract what the other established, so the
    mapping would flicker depending on which statement ran last.
    """
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")
    await upsert_edges(db, [_with_columns(graph_env, ("b", "y"))], provider="execution")

    (edge,) = await _edges(db)
    assert {(c.source_column, c.target_column) for c in await _columns(db, edge.id)} == {
        ("a", "x"),
        ("b", "y"),
    }


async def test_reobserving_the_same_pair_refreshes_it_rather_than_duplicating(graph_env):
    db = graph_env["db"]
    earlier = datetime.now(tz=UTC) - timedelta(days=2)
    await upsert_edges(
        db, [_with_columns(graph_env, ("a", "x"))], provider="execution", observed_at=earlier
    )
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")

    (edge,) = await _edges(db)
    (pair,) = await _columns(db, edge.id)
    assert pair.first_seen_at.replace(tzinfo=UTC) == earlier
    assert pair.last_seen_at.replace(tzinfo=UTC) > earlier


async def test_a_late_arriving_observation_cannot_pull_last_seen_backwards(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")
    (edge,) = await _edges(db)
    (before,) = await _columns(db, edge.id)
    latest = before.last_seen_at

    stale = datetime.now(tz=UTC) - timedelta(days=5)
    await upsert_edges(
        db, [_with_columns(graph_env, ("a", "x"))], provider="execution", observed_at=stale
    )

    (after,) = await _columns(db, edge.id)
    assert after.last_seen_at == latest
    assert after.first_seen_at.replace(tzinfo=UTC) <= stale


async def test_two_providers_keep_their_own_column_detail(graph_env):
    """Column pairs inherit the parent's provenance, so they cannot be confused."""
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")
    await upsert_edges(db, [_with_columns(graph_env, ("b", "x"))], provider="dbt")

    by_provider = {e.provider: e for e in await _edges(db)}
    assert {
        (c.source_column, c.target_column) for c in await _columns(db, by_provider["execution"].id)
    } == {("a", "x")}
    assert {
        (c.source_column, c.target_column) for c in await _columns(db, by_provider["dbt"].id)
    } == {("b", "x")}


async def test_a_later_unsupported_observation_does_not_retract_derived(graph_env):
    """One statement the parser declines does not unmake what another established."""
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")
    await upsert_edges(db, [_with_columns(graph_env, state="unsupported")], provider="execution")

    (edge,) = await _edges(db)
    assert edge.column_lineage == "derived"
    assert len(await _columns(db, edge.id)) == 1


async def test_unsupported_is_recorded_when_nothing_established_columns(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, state="unsupported")], provider="execution")

    (edge,) = await _edges(db)
    assert edge.column_lineage == "unsupported"


# --- column detail follows the edge it refines -------------------------------


async def test_dropping_a_table_takes_its_column_detail_with_it(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")
    await delete_table_lineage(db, graph_env["catalogs"]["warehouse"].id, "analytics", "dim")

    assert await _edges(db) == []
    assert await _columns(db) == []


async def test_dropping_a_schema_takes_its_column_detail_with_it(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")
    await delete_schema_lineage(db, graph_env["catalogs"]["warehouse"].id, "analytics")

    assert await _edges(db) == []
    assert await _columns(db) == []


async def test_purging_a_provider_takes_its_column_detail_with_it(graph_env):
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="dbt")
    await purge_provider(db, provider="dbt")

    assert await _edges(db) == []
    assert await _columns(db) == []


async def test_reconciling_a_run_takes_its_column_detail_with_it(graph_env):
    db = graph_env["db"]
    edge = _with_columns(graph_env, ("a", "x"))
    await upsert_edges(db, [edge], provider="dbt", provider_run_id="run-1")
    await reconcile_provider_run(
        db, provider="dbt", provider_run_id="run-2", target_keys={edge.target.key}
    )

    assert await _edges(db) == []
    assert await _columns(db) == []


async def test_a_rename_carries_column_detail_along(graph_env):
    """Children hang off the edge id, so rewriting the edge in place keeps them."""
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")

    await rekey_table_lineage(
        db,
        graph_env["catalogs"]["warehouse"].id,
        old_schema="analytics",
        old_table="dim",
        new_schema="analytics",
        new_table="dim_v2",
    )

    (edge,) = await _edges(db)
    assert edge.target_table == "dim_v2"
    assert {(c.source_column, c.target_column) for c in await _columns(db, edge.id)} == {("a", "x")}


async def test_a_colliding_rename_merges_column_detail_instead_of_losing_it(graph_env):
    """The one place a rename deletes an edge, so the one place columns can vanish.

    `dim` and `dim_v2` are the same relationship under two names; folding their
    histories together has to fold their column mappings together too.
    """
    db = graph_env["db"]
    await upsert_edges(db, [_with_columns(graph_env, ("a", "x"))], provider="execution")
    await upsert_edges(
        db, [_with_columns(graph_env, ("b", "y"), target_table="dim_v2")], provider="execution"
    )

    await rekey_table_lineage(
        db,
        graph_env["catalogs"]["warehouse"].id,
        old_schema="analytics",
        old_table="dim",
        new_schema="analytics",
        new_table="dim_v2",
    )

    (edge,) = await _edges(db)
    assert {(c.source_column, c.target_column) for c in await _columns(db, edge.id)} == {
        ("a", "x"),
        ("b", "y"),
    }


async def test_two_edges_for_one_pair_keep_both_column_sets(graph_env):
    """Edge identity is the provider and the two keys, so both land on one row.

    Gathering their columns under that row rather than assigning them means the
    second does not silently erase the first — which is what an importer listing
    a relationship twice, or a script writing the same target twice, produces.
    """
    db = graph_env["db"]
    await upsert_edges(
        db,
        [
            _with_columns(graph_env, ("a", "x")),
            _with_columns(graph_env, ("b", "y")),
        ],
        provider="acme",
    )

    (edge,) = await _edges(db)
    assert {(c.source_column, c.target_column) for c in await _columns(db, edge.id)} == {
        ("a", "x"),
        ("b", "y"),
    }
