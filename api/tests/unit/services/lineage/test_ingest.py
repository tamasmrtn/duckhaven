"""Persisting edges: identity, idempotence, provider coexistence, cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.models.lineage import LineageEdge
from api.models.query import Query
from api.services.lineage.ingest import (
    CanonicalEdge,
    delete_schema_lineage,
    delete_table_lineage,
    purge_provider,
    reconcile_provider_run,
    record_execution_lineage,
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
    await upsert_edges(db, [_edge(graph_env)], provider="execution", now=first)
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
