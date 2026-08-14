"""Assembling the graph a caller may see: merging, redaction, workspace clamp."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.lineage import graph as lineage_graph
from api.services.lineage.ingest import CanonicalEdge, upsert_edges
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


async def test_column_lineage_is_reported_as_empty_not_absent(graph_env):
    await _seed_simple(graph_env)
    result = await _graph(graph_env)
    assert result.edges[0].columns == []


async def test_two_providers_merge_into_one_edge_listing_both(graph_env):
    await _seed_simple(graph_env, provider="execution")
    await _seed_simple(graph_env, provider="dbt")

    result = await _graph(graph_env)

    assert len(result.edges) == 1
    assert result.edges[0].providers == ["dbt", "execution"]


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

    assert {e.providers[0] for e in result.edges} == {"dbt", "execution"}
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
