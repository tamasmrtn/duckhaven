"""Pydantic request/response shapes for the lineage endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field, field_validator

# What an edge can say about its column-level detail. "unknown" means nobody
# tried, "unsupported" means somebody tried and could not, and "derived" means
# somebody worked it out — possibly to no columns at all, which is a real answer.
_COLUMN_STATES = frozenset({"unknown", "derived", "unsupported"})


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
    # How many of this dataset's columns take part in the lineage around it —
    # the number of rows it would show if opened. Always present, because it is
    # what lets a *closed* node say whether it is worth opening at all; the
    # mappings themselves still only arrive for the nodes a caller asks about.
    # Zero means the relationships were worked out and none of this dataset's
    # values flow, which is a real answer and not something to offer opening.
    column_count: int = 0


class LineageColumnOut(BaseModel):
    """One ``source column -> target column`` relationship on an edge.

    Means "the value of the target column may be derived from the value of the
    source column" — data flow, not a mention. A column used only to filter rows,
    or only as a join key, is deliberately absent.

    ``providers`` survives the merge that collapses one relationship's per-producer
    rows into a single edge; without it a column pair could not say which producer
    claimed it, which is the one thing somebody deciding whether to trust it needs.
    """

    source_column: str
    target_column: str
    providers: list[str] = Field(default_factory=list)
    # Nothing has re-asserted this mapping within the configured window. Column
    # mappings accumulate rather than being replaced, so a column dropped from a
    # transformation ages out here rather than disappearing.
    stale: bool = False


class LineageProviderOut(BaseModel):
    """What one producer says about a relationship, and how recently it said it.

    Freshness belongs here rather than on the edge because producers keep their
    own cadence. A pair confirmed by a query this morning and by an import that
    stopped running last quarter is one edge with two very different stories, and
    flattening them to a single "last seen" would let the live one vouch for the
    abandoned one.
    """

    name: str
    first_seen_at: datetime
    last_seen_at: datetime
    observation_count: int
    # Nothing has re-asserted this producer's claim within the configured
    # window. A statement about confirmation, not about correctness.
    stale: bool = False
    # Whether *this* producer worked out the column detail. Per-producer for the
    # same reason freshness is: one that can and one that cannot are two different
    # stories about the same relationship, and flattening them would let the one
    # that can vouch for the one that cannot.
    column_lineage: str = "unknown"


class LineageEdgeOut(BaseModel):
    """One relationship, merged across every provider that asserted it.

    Providers are listed rather than reconciled: two producers describing the same
    pair is agreement worth showing, and two producers disagreeing is information,
    not a conflict for the API to silently resolve.

    ``first_seen_at`` / ``last_seen_at`` / ``observation_count`` are the merged
    view — earliest, latest and total across every producer. The per-producer
    breakdown is in ``providers``.
    """

    source_key: str
    target_key: str
    operation: str | None = None
    providers: list[LineageProviderOut]
    confidence: str
    first_seen_at: datetime
    last_seen_at: datetime
    observation_count: int
    # True only when *every* producer's claim is stale. One producer still
    # confirming the relationship keeps the edge current, even if another
    # stopped reporting long ago.
    stale: bool = False
    last_query_id: uuid.UUID | None = None
    # Which columns' values flow along this relationship, merged across every
    # producer that named them. Empty unless the request asked for column detail
    # on one of this edge's endpoints — see ``columns_for`` on the read endpoint —
    # because attaching it unasked would multiply a graph's size by the width of
    # the tables in it.
    columns: list[LineageColumnOut] = Field(default_factory=list)
    # Whether anything worked the columns out: "derived", "unsupported", or
    # "unknown". This is what makes an empty ``columns`` readable. "derived" with
    # no columns is a real answer — the source was read and none of its values
    # reached the target, which is the case table-level lineage cannot express —
    # whereas "unsupported" and "unknown" mean nobody established anything.
    # "derived" if any producer managed it, since one that did is not undone by
    # one that did not.
    column_lineage: str = "unknown"


class LineageGraphOut(BaseModel):
    root: str
    nodes: list[LineageNodeOut]
    edges: list[LineageEdgeOut]
    # True when a cap stopped the walk early, so a partial graph is never
    # mistaken for a complete one.
    truncated: bool = False
    # True when the walk reached lineage this caller may not see at all — a node
    # in a catalog their workspace does not attach — and dropped it.
    #
    # Deliberately a bare flag. A count would say how much is out there, and a
    # placeholder node would say where; both are things the caller is not
    # entitled to know. All it asserts is that "nothing here" would have been the
    # wrong conclusion. (Nodes that are merely ungranted *within* the workspace
    # are a different case: those stay in the graph as `redacted` and do not set
    # this.)
    hidden: bool = False
    # A cap stopped column detail short, so an edge's ``columns`` may be missing
    # some of its mappings. Separate from ``truncated`` because the graph's shape
    # is complete even when this is set — only the detail within it is not.
    columns_truncated: bool = False


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


# Bounded to what the store can hold and to what the native extractor allows
# itself per statement, so an import cannot assert something DuckHaven's own
# extraction would have refused. Over-length names are a 422 rather than a
# truncation error from the database.
_MAX_COLUMN_NAME = 255
_MAX_COLUMNS_PER_EDGE = 2000


class LineageColumnIn(BaseModel):
    source_column: str = Field(min_length=1, max_length=_MAX_COLUMN_NAME)
    target_column: str = Field(min_length=1, max_length=_MAX_COLUMN_NAME)


class LineageEdgeIn(BaseModel):
    """One imported relationship, optionally with its column-level detail.

    ``columns`` is how any producer that knows which columns flow populates the
    same model DuckHaven's own extraction does. Nothing about the graph depends on
    which of the two filled it in.

    ``column_lineage`` is inferred when omitted: ``derived`` if columns were
    given, ``unknown`` otherwise. A producer that checked and found nothing flowing
    — a source only filtered on — says so by sending ``derived`` with an empty
    list, which is a different claim from not having looked.
    """

    source: LineageAssetIn
    target: LineageAssetIn
    operation: str | None = None
    confidence: str = "exact"
    columns: list[LineageColumnIn] = Field(default_factory=list, max_length=_MAX_COLUMNS_PER_EDGE)
    column_lineage: str | None = None

    @field_validator("column_lineage")
    @classmethod
    def _known_state(cls, value: str | None) -> str | None:
        if value is not None and value not in _COLUMN_STATES:
            raise ValueError(f"column_lineage must be one of {sorted(_COLUMN_STATES)}")
        return value

    def resolved_column_lineage(self) -> str:
        if self.column_lineage is not None:
            return self.column_lineage
        return "derived" if self.columns else "unknown"


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
