"""The semantic layer: what business concepts mean, and how they are computed.

A :class:`SemanticModel` is one subject area — "sales", "marketing" — holding the
datasets, dimensions, metrics and relationships that belong together. It is the
unit of three separate things, which is why it exists at all rather than metrics
living loose in a workspace: the unit of **retrieval** (the assistant routes to a
model, then reads it whole), the unit of **publishing** (draft work is invisible
to the assistant until an owner publishes the model), and the unit of **provenance**
(a model belongs to exactly one provider).

Models are keyed by *workspace*, which deliberately differs from
:class:`~api.models.lineage.LineageEdge` and
:class:`~api.models.table_metadata.TableMetadata`. Those are keyed by catalog
because they assert intrinsic facts about data: a relationship between two tables
is true regardless of who is looking. A metric definition is not that kind of
fact. It is an organizational decision about meaning, and two workspaces attaching
the same catalog can legitimately disagree about what "active customer" counts —
as can the same organization before and after a policy change. Ownership, trust
and publishing are all workspace concerns, so the row belongs to the workspace.
The *bindings* still point at catalog objects, so the read boundary and the grant
checks work exactly as they do everywhere else.

Nothing here caches catalog structure (I3). A binding names a catalog, schema and
table; whether those still exist, and whether the columns an expression references
are still there, is resolved against Polaris at validation time and recorded as a
``validation_state`` rather than assumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base

# Only `published` models reach the assistant. `deprecated` stays readable in the
# UI so old links keep working, but is excluded from search and refused by the
# compiler — a definition somebody retired must not keep answering questions.
MODEL_STATUSES = ("draft", "published", "deprecated")

# Whether a binding still resolves against the catalog. "unchecked" is not a
# synonym for "ok": it means nothing has looked since something changed, and the
# compiler treats it as a prompt to revalidate rather than as permission.
VALIDATION_STATES = ("ok", "broken", "unchecked")

# Deliberately no `one_to_many`. Joining toward a non-unique side multiplies fact
# rows and silently inflates every SUM downstream — the single most common way an
# analytical answer is wrong without erroring. Restricting the vocabulary is what
# makes fan-out unrepresentable rather than merely discouraged.
CARDINALITIES = ("many_to_one", "one_to_one")

# `native` is what the UI and the API write. It is a reserved provider name with
# no import adapter, mirroring lineage's reserved `execution`: accepting it over
# the import API would let a client forge a hand-curated, human-owned definition.
NATIVE_PROVIDER = "native"


class SemanticModel(Base):
    """One subject area: the unit of retrieval, publishing and provenance.

    Kept deliberately small, and bounded rather than searched inside. A model
    that fits in front of the assistant whole needs no retrieval within it, and a
    narrow, well-scoped subject area is *itself* the accuracy mechanism: the
    fewer plausible-but-wrong definitions are in scope, the less there is to
    choose wrongly between. Validation warns past the size thresholds rather than
    enforcing them, since the right response is to split the model, which only an
    author can do.
    """

    __tablename__ = "semantic_models"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_semantic_models_slug"),
        Index("ix_semantic_models_workspace_status", "workspace_id", "status"),
        Index("ix_semantic_models_provider_run", "provider", "provider_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # A stable, human-typeable handle used in URLs and in the assistant's tool
    # arguments. Renameable: `id` is the identity, so changing the slug does not
    # orphan bindings, imports or anything that points here. Keying identity on a
    # display name is what forces a system to warn that renaming breaks links —
    # that is the defect this avoids.
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    # `native` for anything authored here; an adapter's name for anything imported.
    # A model has exactly one provider, which is the whole conflict story: imported
    # models are read-only, so there is never a merge to resolve.
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default=NATIVE_PROVIDER, server_default=NATIVE_PROVIDER
    )
    # The import batch this model last arrived in — the unit of reconciliation.
    # Null for native models, which are never batch-reconciled.
    provider_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # When an import last asserted this model. Null for native models: a definition
    # a person wrote does not become less true because nobody re-typed it, so there
    # is deliberately no staleness window here of the kind lineage has.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    datasets: Mapped[list[SemanticDataset]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )
    dimensions: Mapped[list[SemanticDimension]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )
    metrics: Mapped[list[SemanticMetric]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )
    relationships: Mapped[list[SemanticRelationship]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )


class SemanticDataset(Base):
    """A logical table, named in business terms, bound to one physical table.

    This is also the model's *entity* concept. A separate entity would only earn
    its keep if join paths were *derived* from entities rather than declared;
    DuckHaven declares its joins, so an entity would be a second name for the same
    thing and one more choice for an author to get wrong.

    ``primary_key`` is not decoration: it is what makes a ``many_to_one``
    relationship provable. Validation rejects a relationship whose right-hand
    columns are not this dataset's primary key, because such a relationship claims
    a uniqueness the data does not have and would fan out.
    """

    __tablename__ = "semantic_datasets"
    __table_args__ = (
        UniqueConstraint("model_id", "name", name="uq_semantic_datasets_name"),
        # The reverse index: "which metrics depend on this table?" — asked by the
        # table detail page and by the drop-table confirmation.
        Index(
            "ix_semantic_datasets_binding",
            "catalog_id",
            "schema_name",
            "table_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_models.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Alternative words a person might use for this entity ("clients", "accounts").
    # Matched by the assistant's search before any expression is considered.
    synonyms: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), nullable=False
    )
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Column names, as a list. Empty/null means "no key declared", which makes this
    # dataset ineligible as the right-hand side of a relationship.
    primary_key: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    validation_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unchecked", server_default="unchecked"
    )
    validation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    model: Mapped[SemanticModel] = relationship(back_populates="datasets")


class SemanticDimension(Base):
    """A way to slice a metric: a categorical attribute or a time axis.

    ``kind="time"`` plus ``time_grains`` is what stops "monthly revenue" from
    becoming a ``GROUP BY`` on a raw timestamp, and stops a grain the column cannot
    support from being requested at all.

    ``sample_values`` exists for one specific failure: a user asks for customers
    "in the US", the stored value is ``'United States'``, and the query returns
    zero rows with no error anywhere. A short list of real values is enough to
    resolve what somebody said to what is actually stored, which is why it is
    worth carrying even though it is only a hint.
    """

    __tablename__ = "semantic_dimensions"
    __table_args__ = (
        UniqueConstraint("model_id", "name", name="uq_semantic_dimensions_name"),
        Index("ix_semantic_dimensions_dataset", "dataset_id"),
        CheckConstraint(
            "kind IN ('categorical', 'time')",
            name="ck_semantic_dimensions_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_models.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_datasets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    synonyms: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="categorical", server_default="categorical"
    )
    # A scalar SQL expression over the bound table's columns. Defaults to the bare
    # column name; anything more (a CASE, a COALESCE, a cast) is parsed and its
    # identifiers checked at validation time.
    expr: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The grains this time dimension supports, e.g. ["day", "month", "quarter"].
    # Only meaningful when kind == "time".
    time_grains: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    # Marks the dataset's default time axis, used when a metric does not name one.
    is_default_time: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # A short list of real values, for resolving what the user said to what is
    # stored. Bounded at write time — this is a hint, not a copy of the column.
    sample_values: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    validation_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unchecked", server_default="unchecked"
    )
    validation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[SemanticModel] = relationship(back_populates="dimensions")


class SemanticMetric(Base):
    """The authoritative answer to a business question, and how it is computed.

    ``agg`` and ``expr`` are separate rather than one free-form aggregate string
    so the compiler builds the aggregation itself. That is the whole point: the
    language model chooses *which* metric, never *how* it is calculated, so
    ``SUM(order_items.price)`` is not reachable when the published definition is a
    sum over ``orders.total_amount``.

    ``time_dimension_id`` is the highest-value field here. "Revenue last month"
    measured on ``created_at`` instead of ``order_date`` produces a different
    number and no error, which makes it the most expensive kind of wrong answer —
    the kind nobody notices. Binding the metric to its measurement axis removes
    the choice.

    There is deliberately **no separate measure concept** — no row-level "fact"
    sitting underneath the metric as a second thing to define. That split only
    earns its keep once derived and ratio metrics compose out of measures, which
    V1 does not have. Until then it is two concepts where both an author and the
    assistant must pick the right one, which is exactly the ambiguity this table
    exists to remove. Reinstate it when composition arrives.
    """

    __tablename__ = "semantic_metrics"
    __table_args__ = (
        UniqueConstraint("model_id", "name", name="uq_semantic_metrics_name"),
        Index("ix_semantic_metrics_dataset", "dataset_id"),
        CheckConstraint(
            "agg IN ('sum', 'count', 'count_distinct', 'avg', 'min', 'max')",
            name="ck_semantic_metrics_agg",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_models.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_datasets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "turnover", "GMV", "top line" — how people actually ask for this.
    synonyms: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    agg: Mapped[str] = mapped_column(String(20), nullable=False)
    # The scalar expression to aggregate. Null only for `count`, which counts rows.
    expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A boolean expression applied to *this metric only*, compiled as a
    # `FILTER (WHERE ...)` clause so several metrics with different filters compose
    # in one SELECT. This is where "excludes test accounts" lives so that it applies
    # every time rather than whenever somebody remembers it.
    filter: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which time axis this metric is measured on. SET NULL rather than CASCADE:
    # deleting the dimension must not delete the metric, it must make the metric
    # visibly incomplete so somebody fixes it.
    time_dimension_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("semantic_dimensions.id", ondelete="SET NULL"), nullable=True
    )
    # Surfaced with every answer this metric produces. The place for "excludes
    # internal orders" or "restated before 2024" — the things a reader needs at the
    # moment they see the number, not a month later.
    caveat: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    validation_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unchecked", server_default="unchecked"
    )
    validation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model: Mapped[SemanticModel] = relationship(back_populates="metrics")


class SemanticRelationship(Base):
    """A declared join between two datasets, in the direction that is safe.

    Joins are *declared*, not inferred. Inferring them from key types means
    policing a matrix of which combinations are legal; declaring them means the
    illegal combination is simply never written down. The ``cardinality``
    vocabulary carries the rule: only ``many_to_one`` and ``one_to_one`` exist, so
    traversal always moves from a fact toward something unique and the fact table's
    grain survives the join.
    """

    __tablename__ = "semantic_relationships"
    __table_args__ = (
        UniqueConstraint("model_id", "name", name="uq_semantic_relationships_name"),
        Index("ix_semantic_relationships_left", "left_dataset_id"),
        CheckConstraint(
            "cardinality IN ('many_to_one', 'one_to_one')",
            name="ck_semantic_relationships_cardinality",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_models.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The "many" side for many_to_one: traversal starts here and moves right.
    left_dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_datasets.id", ondelete="CASCADE"), nullable=False
    )
    right_dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_datasets.id", ondelete="CASCADE"), nullable=False
    )
    # `[{"left": "customer_id", "right": "id"}, ...]` — column names on each side.
    join_columns: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    cardinality: Mapped[str] = mapped_column(
        String(20), nullable=False, default="many_to_one", server_default="many_to_one"
    )
    validation_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unchecked", server_default="unchecked"
    )
    validation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[SemanticModel] = relationship(back_populates="relationships")
