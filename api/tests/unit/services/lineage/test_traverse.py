"""Bounded graph walks: direction, depth, caps, cycles, shared nodes."""

from __future__ import annotations

import pytest

from api.services.lineage import traverse
from api.services.lineage.ingest import CanonicalEdge, upsert_edges
from api.services.lineage.keys import internal_ref


@pytest.fixture
def cat(graph_env):
    """Shorthand: every node in these tests lives in one catalog."""
    return graph_env["catalogs"]["warehouse"].id


def key(cat_id, table: str) -> str:
    return internal_ref(cat_id, "analytics", table).key


async def link(db, cat_id, *chain: str, provider: str = "execution") -> None:
    """Persist a -> b -> c ... as consecutive edges."""
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(cat_id, "analytics", a),
                target=internal_ref(cat_id, "analytics", b),
                operation="create_table_as",
            )
            for a, b in zip(chain, chain[1:], strict=False)
        ],
        provider=provider,
    )


async def test_upstream_walks_backwards_with_negative_distances(graph_env, cat):
    db = graph_env["db"]
    await link(db, cat, "a", "b", "c")

    result = await traverse.walk(db, root_key=key(cat, "c"), direction="upstream", depth=2)

    assert result.distances[key(cat, "b")] == -1
    assert result.distances[key(cat, "a")] == -2
    assert result.distances[key(cat, "c")] == 0


async def test_downstream_walks_forwards_with_positive_distances(graph_env, cat):
    db = graph_env["db"]
    await link(db, cat, "a", "b", "c")

    result = await traverse.walk(db, root_key=key(cat, "a"), direction="downstream", depth=2)

    assert result.distances[key(cat, "b")] == 1
    assert result.distances[key(cat, "c")] == 2


async def test_both_directions_signs_each_side(graph_env, cat):
    db = graph_env["db"]
    await link(db, cat, "up", "root", "down")

    result = await traverse.walk(db, root_key=key(cat, "root"), direction="both", depth=1)

    assert result.distances[key(cat, "up")] == -1
    assert result.distances[key(cat, "down")] == 1


async def test_depth_bounds_the_walk(graph_env, cat):
    db = graph_env["db"]
    await link(db, cat, "a", "b", "c", "d")

    result = await traverse.walk(db, root_key=key(cat, "a"), direction="downstream", depth=1)

    assert key(cat, "b") in result.distances
    assert key(cat, "c") not in result.distances


async def test_depth_is_clamped_to_the_maximum(graph_env, cat):
    db = graph_env["db"]
    chain = [f"t{i}" for i in range(10)]
    await link(db, cat, *chain)

    result = await traverse.walk(db, root_key=key(cat, "t0"), direction="downstream", depth=99)

    # MAX_DEPTH hops from the root, plus the root itself.
    assert max(result.distances.values()) == traverse.MAX_DEPTH


async def test_a_diamond_visits_the_shared_node_once(graph_env, cat):
    db = graph_env["db"]
    await link(db, cat, "src", "left")
    await link(db, cat, "src", "right")
    await link(db, cat, "left", "sink")
    await link(db, cat, "right", "sink")

    result = await traverse.walk(db, root_key=key(cat, "sink"), direction="upstream", depth=3)

    assert result.distances[key(cat, "src")] == -2
    assert len([e for e in result.edges if e.target_table == "sink"]) == 2


async def test_a_cycle_terminates(graph_env, cat):
    db = graph_env["db"]
    await link(db, cat, "a", "b")
    await link(db, cat, "b", "a")

    result = await traverse.walk(db, root_key=key(cat, "a"), direction="downstream", depth=5)

    assert set(result.distances) == {key(cat, "a"), key(cat, "b")}


async def test_node_cap_marks_the_walk_truncated(graph_env, cat, monkeypatch):
    db = graph_env["db"]
    monkeypatch.setattr(traverse, "MAX_NODES", 3)
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(cat, "analytics", "hub"),
                target=internal_ref(cat, "analytics", f"leaf{i}"),
            )
            for i in range(10)
        ],
        provider="execution",
    )

    result = await traverse.walk(db, root_key=key(cat, "hub"), direction="downstream", depth=1)

    assert result.truncated is True
    assert len(result.distances) <= 3


async def test_provider_filter_narrows_the_walk(graph_env, cat):
    db = graph_env["db"]
    await link(db, cat, "a", "b", provider="execution")
    await link(db, cat, "c", "b", provider="dbt")

    result = await traverse.walk(
        db, root_key=key(cat, "b"), direction="upstream", depth=1, providers=["dbt"]
    )

    assert key(cat, "c") in result.distances
    assert key(cat, "a") not in result.distances


async def test_an_isolated_table_walks_to_just_itself(graph_env, cat):
    result = await traverse.walk(graph_env["db"], root_key=key(cat, "lonely"))
    assert result.distances == {key(cat, "lonely"): 0}
    assert result.edges == []
