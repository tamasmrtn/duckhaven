"""Pydantic request/response shapes for the lineage endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field


class LineageNodeOut(BaseModel):
    """One dataset in the graph.

    ``kind`` says how much of it the caller may see: ``table`` is fully named,
    ``external`` is an asset outside DuckHaven named by whoever imported it, and
    ``redacted`` is a node in a scoped catalog the caller holds no grant on —
    present so the graph keeps its shape and its distances, but carrying no names.
    """

    key: str
    kind: str
    catalog: str | None = None
    schema_name: str | None = None
    table: str | None = None
    system: str | None = None
    # Signed: negative upstream of the root, positive downstream, 0 for the root.
    distance: int


class LineageColumnOut(BaseModel):
    source_column: str
    target_column: str


class LineageEdgeOut(BaseModel):
    """One relationship, merged across every provider that asserted it.

    Providers are listed rather than reconciled: two producers describing the same
    pair is agreement worth showing, and two producers disagreeing is information,
    not a conflict for the API to silently resolve.
    """

    source_key: str
    target_key: str
    operation: str | None = None
    providers: list[str]
    confidence: str
    first_seen_at: datetime
    last_seen_at: datetime
    observation_count: int
    last_query_id: uuid.UUID | None = None
    # Column-level lineage is not derived yet, so this is always empty. Present
    # from the start so adding it later changes no contract.
    columns: list[LineageColumnOut] = Field(default_factory=list)


class LineageGraphOut(BaseModel):
    root: str
    nodes: list[LineageNodeOut]
    edges: list[LineageEdgeOut]
    # True when a cap stopped the walk early, so a partial graph is never
    # mistaken for a complete one.
    truncated: bool = False


class LineageAssetIn(BaseModel):
    """One endpoint of an imported edge.

    ``catalog`` names a DuckHaven catalog; ``system`` names something outside
    DuckHaven. Exactly one of the two is expected — an endpoint with neither
    cannot be resolved, and one with both is ambiguous. ``schema`` is accepted as
    a spelling of ``schema_name`` because that is what a producer's own manifest
    calls it.
    """

    catalog: str | None = None
    system: str | None = None
    schema_name: str = Field(validation_alias=AliasChoices("schema_name", "schema"))
    table: str

    model_config = {"populate_by_name": True}


class LineageEdgeIn(BaseModel):
    source: LineageAssetIn
    target: LineageAssetIn
    operation: str | None = None
    confidence: str = "exact"


class LineageImportIn(BaseModel):
    """A batch of edges from one producer.

    ``reconcile="provider_run"`` prunes this provider's stale edges into the
    targets this payload names — scoped that way so a partial run cannot delete
    lineage for assets it did not rebuild. It requires ``run_id``.
    """

    provider: str
    run_id: str | None = None
    reconcile: str = "none"
    edges: list[LineageEdgeIn]


class LineageSkippedOut(BaseModel):
    """An edge endpoint that could not be resolved, and why.

    Returned alongside a 200 rather than failing the request: a manifest with a
    handful of unresolvable refs should still import everything else.
    """

    ref: str
    reason: str


class LineageImportOut(BaseModel):
    created: int
    updated: int
    removed: int
    skipped: list[LineageSkippedOut] = Field(default_factory=list)
