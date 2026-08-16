"""Assembling the graph a caller may see: merging, redaction, workspace clamp."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.config import settings
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.lineage import graph as lineage_graph
from api.services.lineage.columns import ColumnPair
from api.services.lineage.ingest import (
    CanonicalEdge,
    record_execution_lineage,
    upsert_edges,
)
from api.services.lineage.keys import external_ref, internal_ref
from api.services.workspace import resolve_workspace_catalogs


async def _graph(env, *, table="dim", principal=None, **kw):
    db = env["db"]
    catalogs = await resolve_workspace_catalogs(db, env["workspace"].id)
    return await lineage_graph.table_lineage(
        db,
        workspace_id=env["workspace"].id,
        principal_id=principal or env["user"].id,
        catalogs=catalogs,
        catalog=env["catalogs"]["warehouse"],
        schema="analytics",
        table=table,
        **kw,
    )


def _by_kind(result):
    return {n.kind for n in result.nodes}


async def _seed_simple(env, provider="execution"):
    return await upsert_edges(
        env["db"],
        [
            CanonicalEdge(
                source=internal_ref(env["catalogs"]["raw"].id, "analytics", "src"),
                target=internal_ref(env["catalogs"]["warehouse"].id, "analytics", "dim"),
                operation="create_table_as",
            )
        ],
        provider=provider,
    )


# --- assembly ---------------------------------------------------------------


async def test_graph_names_both_endpoints(graph_env):
    await _seed_simple(graph_env)
    result = await _graph(graph_env)

    names = {(n.catalog, n.schema_name, n.table) for n in result.nodes}
    assert names == {("raw", "analytics", "src"), ("warehouse", "analytics", "dim")}
    assert result.truncated is False


async def test_root_is_present_even_with_no_lineage(graph_env):
    # An empty graph for a real table is an answer, not a 404.
    result = await _graph(graph_env, table="untouched")
    assert [n.table for n in result.nodes] == ["untouched"]
    assert result.edges == []


async def test_column_detail_is_absent_unless_it_was_asked_for(graph_env):
    """The default response is the table graph, unchanged and no bigger.

    Column detail scales with how wide the tables are rather than how many nodes
    the walk found, so attaching it unasked would make the cost of opening the
    lineage tab depend on something nobody is looking at.
    """
    await _seed_simple(graph_env)
    result = await _graph(graph_env)
    assert result.edges[0].columns == []
    assert result.columns_truncated is False


async def test_two_providers_merge_into_one_edge_listing_both(graph_env):
    await _seed_simple(graph_env, provider="execution")
    await _seed_simple(graph_env, provider="dbt")

    result = await _graph(graph_env)

    assert len(result.edges) == 1
    assert [p.name for p in result.edges[0].providers] == ["dbt", "execution"]


async def test_providers_that_disagree_both_appear(graph_env):
    # dbt says raw.src -> dim; execution says raw.other -> dim. Neither wins.
    db = graph_env["db"]
    await _seed_simple(graph_env, provider="dbt")
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(graph_env["catalogs"]["raw"].id, "analytics", "other"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
            )
        ],
        provider="execution",
    )

    result = await _graph(graph_env)

    assert {e.providers[0].name for e in result.edges} == {"dbt", "execution"}
    assert len(result.edges) == 2


async def test_external_source_appears_as_an_external_node(graph_env):
    await upsert_edges(
        graph_env["db"],
        [
            CanonicalEdge(
                source=external_ref("crm_pg", "public", "customers"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
                operation="model",
            )
        ],
        provider="dbt",
    )

    result = await _graph(graph_env)
    external = next(n for n in result.nodes if n.kind == "external")
    assert external.system == "crm_pg"
    assert external.table == "customers"
    assert external.catalog is None


# --- workspace boundary -----------------------------------------------------


async def test_a_catalog_the_workspace_does_not_attach_is_pruned(graph_env):
    """Out of scope, not merely ungranted — so the node disappears entirely."""
    db = graph_env["db"]
    other = Catalog(
        slug="elsewhere",
        name="elsewhere",
        polaris_name="pol_elsewhere",
        storage_backend_id=graph_env["catalogs"]["warehouse"].storage_backend_id,
        created_by=graph_env["user"].id,
    )
    db.add(other)
    await db.flush()
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(other.id, "analytics", "hidden"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
            )
        ],
        provider="execution",
    )

    result = await _graph(graph_env)

    assert [n.table for n in result.nodes] == ["dim"]
    assert result.edges == []  # the edge went with the pruned endpoint


# --- redaction --------------------------------------------------------------


async def _make_scoped_stranger(env):
    """A member with no grants, in a workspace whose `raw` catalog is scoped."""
    db = env["db"]
    await db.execute(
        sa.update(WorkspaceCatalog)
        .where(
            WorkspaceCatalog.workspace_id == env["workspace"].id,
            WorkspaceCatalog.catalog_id == env["catalogs"]["raw"].id,
        )
        .values(access_mode="scoped")
    )
    stranger = User(email=f"s-{uuid.uuid4().hex[:8]}@example.com", name="Stranger", role="user")
    db.add(stranger)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=env["workspace"].id, user_id=stranger.id, role="reader"))
    await db.flush()
    return stranger


async def test_a_node_without_a_grant_is_redacted_not_dropped(graph_env):
    await _seed_simple(graph_env)
    stranger = await _make_scoped_stranger(graph_env)

    result = await _graph(graph_env, principal=stranger.id)

    assert "redacted" in _by_kind(result)
    hidden = next(n for n in result.nodes if n.kind == "redacted")
    assert hidden.table is None and hidden.schema_name is None and hidden.catalog is None
    assert "src" not in hidden.key
    # The path through it survives, which is the whole point of redacting.
    assert len(result.edges) == 1


async def test_redacted_keys_are_stable_across_requests(graph_env):
    await _seed_simple(graph_env)
    stranger = await _make_scoped_stranger(graph_env)

    first = await _graph(graph_env, principal=stranger.id)
    second = await _graph(graph_env, principal=stranger.id)

    def redacted_keys(result):
        return sorted(n.key for n in result.nodes if n.kind == "redacted")

    assert redacted_keys(first) == redacted_keys(second)


async def test_a_grant_reveals_the_node(graph_env):
    db = graph_env["db"]
    await _seed_simple(graph_env)
    stranger = await _make_scoped_stranger(graph_env)
    db.add(
        CatalogGrant(
            user_id=stranger.id,
            catalog_id=graph_env["catalogs"]["raw"].id,
            schema_name="analytics",
            table_name="src",
            tier="metadata",
        )
    )
    await db.flush()

    result = await _graph(graph_env, principal=stranger.id)

    assert "redacted" not in _by_kind(result)
    assert {n.table for n in result.nodes} == {"src", "dim"}


async def test_an_open_catalog_is_never_redacted(graph_env):
    # `raw` stays open here, so a member with no grants still sees the names —
    # matching how the rest of the grant system no-ops for open attachments.
    await _seed_simple(graph_env)
    db = graph_env["db"]
    stranger = User(email=f"o-{uuid.uuid4().hex[:8]}@example.com", name="Open", role="user")
    db.add(stranger)
    await db.flush()
    db.add(
        WorkspaceMember(workspace_id=graph_env["workspace"].id, user_id=stranger.id, role="reader")
    )
    await db.flush()

    result = await _graph(graph_env, principal=stranger.id)
    assert "redacted" not in _by_kind(result)


# --- direction and depth pass through ---------------------------------------


@pytest.mark.parametrize("direction", ["upstream", "downstream", "both"])
async def test_direction_is_honoured(graph_env, direction):
    await _seed_simple(graph_env)
    result = await _graph(graph_env, direction=direction)
    tables = {n.table for n in result.nodes}
    if direction == "downstream":
        assert tables == {"dim"}  # nothing is built *from* dim
    else:
        assert tables == {"src", "dim"}


async def test_a_redacted_endpoint_withholds_the_query_link(graph_env):
    """The query behind an edge is readable by any workspace member and its SQL
    names every table it touched, so handing over the link would undo the
    redaction sitting next to it."""
    db = graph_env["db"]
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(graph_env["catalogs"]["raw"].id, "analytics", "src"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
                operation="create_table_as",
            )
        ],
        provider="execution",
        last_query_id=graph_env["workspace"].id,  # any uuid; only presence matters
    )
    # Baseline first: while the catalog is still `open`, the link is served.
    visible = await _graph(graph_env)
    assert visible.edges[0].last_query_id is not None

    # Scoping `raw` and asking as a principal with no grant on it redacts the
    # source — and must take the link with it.
    stranger = await _make_scoped_stranger(graph_env)
    hidden = await _graph(graph_env, principal=stranger.id)

    assert "redacted" in _by_kind(hidden)
    assert hidden.edges[0].last_query_id is None


# --- completeness -----------------------------------------------------------


async def _unattached_catalog(env):
    """A catalog that exists but that the requesting workspace does not attach."""
    db = env["db"]
    other = Catalog(
        slug=f"elsewhere-{uuid.uuid4().hex[:6]}",
        name="elsewhere",
        polaris_name=f"pol_elsewhere_{uuid.uuid4().hex[:6]}",
        storage_backend_id=env["catalogs"]["warehouse"].storage_backend_id,
        created_by=env["user"].id,
    )
    db.add(other)
    await db.flush()
    return other


async def test_a_fully_visible_graph_reports_itself_complete(graph_env):
    await _seed_simple(graph_env)
    result = await _graph(graph_env)
    assert result.hidden is False


async def test_a_graph_with_no_lineage_at_all_is_not_reported_as_incomplete(graph_env):
    """The distinction the flag exists to draw. An empty graph and a graph whose
    every edge was withheld must not answer the same way."""
    result = await _graph(graph_env)
    assert (result.edges, result.hidden) == ([], False)


async def test_lineage_reaching_outside_the_workspace_is_reported_as_incomplete(graph_env):
    db = graph_env["db"]
    other = await _unattached_catalog(graph_env)
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(other.id, "analytics", "somewhere_else"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
            )
        ],
        provider="execution",
    )

    result = await _graph(graph_env)

    assert result.hidden is True
    # "Nothing upstream" and "something upstream you cannot see" lead to
    # opposite decisions, and only the flag separates them here.
    assert result.edges == []


async def test_an_incomplete_graph_names_nothing_it_withheld(graph_env):
    db = graph_env["db"]
    other = await _unattached_catalog(graph_env)
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(other.id, "analytics", "salaries"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
            )
        ],
        provider="execution",
    )

    result = await _graph(graph_env)

    serialized = result.model_dump_json()
    for secret in ("salaries", other.slug, str(other.id)):
        assert secret not in serialized


async def test_a_redacted_node_is_not_an_incomplete_graph(graph_env):
    """Redaction keeps the node, its position and its distances — the graph is
    whole, just partly nameless. Only an outright drop is incompleteness."""
    await _seed_simple(graph_env)
    stranger = await _make_scoped_stranger(graph_env)

    result = await _graph(graph_env, principal=stranger.id)

    assert "redacted" in _by_kind(result)
    assert result.hidden is False


# --- freshness --------------------------------------------------------------


async def _seed_aged(env, *, provider, days_ago, source="src"):
    return await upsert_edges(
        env["db"],
        [
            CanonicalEdge(
                source=internal_ref(env["catalogs"]["raw"].id, "analytics", source),
                target=internal_ref(env["catalogs"]["warehouse"].id, "analytics", "dim"),
                operation="create_table_as",
            )
        ],
        provider=provider,
        observed_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
    )


async def test_a_recently_observed_relationship_is_not_stale(graph_env):
    await _seed_aged(graph_env, provider="execution", days_ago=1)
    result = await _graph(graph_env)
    assert result.edges[0].stale is False
    assert result.edges[0].providers[0].stale is False


async def test_a_relationship_nothing_has_re_asserted_is_stale(graph_env):
    await _seed_aged(graph_env, provider="dbt", days_ago=200)
    result = await _graph(graph_env)
    assert result.edges[0].stale is True
    assert result.edges[0].providers[0].stale is True
    # Still in the graph. Marking it is the point; removing it would be the
    # bigger lie.
    assert result.edges[0].source_key.endswith("/src")


async def test_a_live_producer_does_not_vouch_for_an_abandoned_one(graph_env):
    """The whole reason freshness is per-producer. An import that stopped running
    last quarter must stay visibly stale even though a query confirmed the same
    pair this morning — and must not drag the edge down with it either."""
    await _seed_aged(graph_env, provider="dbt", days_ago=200)
    await _seed_aged(graph_env, provider="execution", days_ago=1)

    (edge,) = (await _graph(graph_env)).edges

    assert {(p.name, p.stale) for p in edge.providers} == {("dbt", True), ("execution", False)}
    assert edge.stale is False, "one producer still confirming keeps the edge current"


async def test_an_edge_is_stale_only_when_every_producer_is(graph_env):
    await _seed_aged(graph_env, provider="dbt", days_ago=200)
    await _seed_aged(graph_env, provider="execution", days_ago=200)

    (edge,) = (await _graph(graph_env)).edges

    assert all(p.stale for p in edge.providers)
    assert edge.stale is True


async def test_freshness_can_be_switched_off_entirely(graph_env, monkeypatch):
    monkeypatch.setattr(settings, "lineage_stale_after_days", 0)
    await _seed_aged(graph_env, provider="dbt", days_ago=3650)

    (edge,) = (await _graph(graph_env)).edges

    assert edge.stale is False
    assert edge.providers[0].stale is False


async def test_each_producer_keeps_its_own_window_and_count(graph_env):
    await _seed_aged(graph_env, provider="dbt", days_ago=200)
    await _seed_aged(graph_env, provider="dbt", days_ago=150)
    await _seed_aged(graph_env, provider="execution", days_ago=2)

    (edge,) = (await _graph(graph_env)).edges
    by_name = {p.name: p for p in edge.providers}

    assert by_name["dbt"].observation_count == 2
    assert by_name["execution"].observation_count == 1
    # The merged view is still the widest window across all of them.
    assert edge.observation_count == 3
    assert edge.first_seen_at == min(p.first_seen_at for p in edge.providers)
    assert edge.last_seen_at == max(p.last_seen_at for p in edge.providers)


async def test_a_backfilled_historical_relationship_is_stale_on_first_read(graph_env):
    """Phase 4 meeting Phase 3: replaying six months of history must not produce
    six months of relationships that all look confirmed today."""
    db = graph_env["db"]
    ran_at = datetime.now(tz=UTC) - timedelta(days=180)
    query = Query(
        workspace_id=graph_env["workspace"].id,
        sql="CREATE TABLE warehouse.analytics.dim AS SELECT * FROM raw.analytics.src",
        status="done",
        started_at=ran_at,
        finished_at=ran_at,
        active_catalog="warehouse",
    )
    db.add(query)
    await db.flush()
    await record_execution_lineage(db, query)

    (edge,) = (await _graph(graph_env)).edges

    assert edge.stale is True
    assert edge.last_seen_at.replace(tzinfo=UTC) == ran_at


# --- column detail ----------------------------------------------------------


async def _seed_columns(env, *pairs, provider="execution", state="derived", source="src"):
    return await upsert_edges(
        env["db"],
        [
            CanonicalEdge(
                source=internal_ref(env["catalogs"]["raw"].id, "analytics", source),
                target=internal_ref(env["catalogs"]["warehouse"].id, "analytics", "dim"),
                operation="create_table_as",
                column_lineage=state,
                columns=tuple(ColumnPair(source_column=s, target_column=t) for s, t in pairs),
            )
        ],
        provider=provider,
    )


def _dim_key(env):
    return internal_ref(env["catalogs"]["warehouse"].id, "analytics", "dim").key


async def test_asking_for_a_node_attaches_its_column_detail(graph_env):
    await _seed_columns(graph_env, ("a", "x"), ("b", "x"))

    result = await _graph(graph_env, columns_for={_dim_key(graph_env)})

    edge = result.edges[0]
    assert edge.column_lineage == "derived"
    assert [(c.source_column, c.target_column) for c in edge.columns] == [("a", "x"), ("b", "x")]
    assert edge.columns[0].providers == ["execution"]


async def test_asking_for_an_unrelated_node_attaches_nothing(graph_env):
    await _seed_columns(graph_env, ("a", "x"))

    unrelated = internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "elsewhere")
    result = await _graph(graph_env, columns_for={unrelated.key})

    assert result.edges[0].columns == []


async def test_derived_with_no_columns_is_distinguishable_from_not_knowing(graph_env):
    """The distinction the whole state field exists for.

    An edge that was analysed and moves no column values — a source only filtered
    on — has to read differently from one nobody could analyse, because the first
    means "nothing flows here" and the second means "we cannot say".
    """
    await _seed_columns(graph_env)

    result = await _graph(graph_env, columns_for={_dim_key(graph_env)})

    assert result.edges[0].column_lineage == "derived"
    assert result.edges[0].columns == []


async def test_an_unsupported_edge_says_so(graph_env):
    await _seed_columns(graph_env, state="unsupported")

    result = await _graph(graph_env, columns_for={_dim_key(graph_env)})

    assert result.edges[0].column_lineage == "unsupported"
    assert result.edges[0].columns == []


async def test_two_providers_naming_the_same_mapping_make_one_entry(graph_env):
    """Agreement between producers is worth showing, not worth duplicating."""
    await _seed_columns(graph_env, ("a", "x"), provider="execution")
    await _seed_columns(graph_env, ("a", "x"), provider="dbt")

    result = await _graph(graph_env, columns_for={_dim_key(graph_env)})

    (column,) = result.edges[0].columns
    assert (column.source_column, column.target_column) == ("a", "x")
    assert column.providers == ["dbt", "execution"]


async def test_two_providers_naming_different_mappings_keep_both(graph_env):
    """Disagreement is information; the API reports it rather than picking."""
    await _seed_columns(graph_env, ("a", "x"), provider="execution")
    await _seed_columns(graph_env, ("b", "x"), provider="dbt")

    result = await _graph(graph_env, columns_for={_dim_key(graph_env)})

    assert [(c.source_column, c.providers) for c in result.edges[0].columns] == [
        ("a", ["execution"]),
        ("b", ["dbt"]),
    ]


async def test_a_provider_that_worked_columns_out_is_not_undone_by_one_that_did_not(graph_env):
    await _seed_columns(graph_env, ("a", "x"), provider="execution")
    await _seed_columns(graph_env, state="unsupported", provider="dbt")

    result = await _graph(graph_env, columns_for={_dim_key(graph_env)})

    edge = result.edges[0]
    assert edge.column_lineage == "derived"
    assert {p.name: p.column_lineage for p in edge.providers} == {
        "execution": "derived",
        "dbt": "unsupported",
    }


async def test_a_mapping_nothing_re_asserts_goes_stale(graph_env):
    old = datetime.now(tz=UTC) - timedelta(days=settings.lineage_stale_after_days + 5)
    await upsert_edges(
        graph_env["db"],
        [
            CanonicalEdge(
                source=internal_ref(graph_env["catalogs"]["raw"].id, "analytics", "src"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "dim"),
                column_lineage="derived",
                columns=(ColumnPair(source_column="a", target_column="x"),),
            )
        ],
        provider="execution",
        observed_at=old,
    )

    result = await _graph(graph_env, columns_for={_dim_key(graph_env)})

    assert result.edges[0].columns[0].stale is True


async def test_a_redacted_endpoint_withholds_the_column_names(graph_env):
    """Column names of a restricted table are exactly what redaction holds back.

    Serving them beside a node that deliberately carries no name would give away
    the thing the redaction exists to keep, in more detail than the table name
    itself would have.
    """
    await _seed_columns(graph_env, ("secret_column", "x"))
    key = _dim_key(graph_env)

    visible = await _graph(graph_env, columns_for={key})
    assert visible.edges[0].columns

    stranger = await _make_scoped_stranger(graph_env)
    hidden = await _graph(graph_env, principal=stranger.id, columns_for={key})

    assert "redacted" in _by_kind(hidden)
    assert hidden.edges[0].columns == []


async def test_the_column_cap_truncates_and_says_so(graph_env):
    from api.services.lineage import graph as graph_module

    await _seed_columns(graph_env, ("a", "x"), ("b", "y"), ("c", "z"))

    original = graph_module.MAX_COLUMN_PAIRS
    graph_module.MAX_COLUMN_PAIRS = 2
    try:
        result = await _graph(graph_env, columns_for={_dim_key(graph_env)})
    finally:
        graph_module.MAX_COLUMN_PAIRS = original

    assert result.columns_truncated is True
    assert len(result.edges[0].columns) == 2
    # The graph's own shape is untouched — only the detail inside it was capped.
    assert result.truncated is False
