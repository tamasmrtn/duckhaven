"""Semantic layer: metrics, dimensions and the joins between them

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-17

DuckHaven's catalog knows a column is called ``total_amount`` and holds a decimal.
It does not know that summing it is what the business calls revenue, that the sum
excludes test orders, or that "last month" means ``order_date`` and not
``created_at``. The assistant has had to guess all three, and a guess that lands
on the wrong column produces a plausible number with no error anywhere. These five
tables are where those answers live.

Five tables rather than one wide one because they have genuinely different
lifecycles: a model is published, a dataset is bound and validated, a metric is
owned, a relationship is a constraint. Collapsing them would mean a status column
that means something different per row kind.

Keyed by workspace, unlike ``lineage_edges`` and ``table_metadata``, and the
difference is deliberate rather than an inconsistency. Those tables assert
intrinsic facts about data — a relationship between two tables is true no matter
who asks — so a catalog attached twice has one lineage graph. A metric definition
is an organizational decision about meaning, and two workspaces sharing a catalog
can legitimately disagree about what "active customer" counts. Ownership and
publishing are workspace concerns too, so the row belongs to the workspace while
the *binding* still points at a catalog object.

Two constraints in here are load-bearing rather than defensive.
``ck_semantic_relationships_cardinality`` admits only ``many_to_one`` and
``one_to_one``: joining toward a non-unique side multiplies fact rows and inflates
every ``SUM`` downstream without erroring, so the vocabulary simply has no word
for it. And ``semantic_metrics.time_dimension_id`` is ``SET NULL`` rather than
``CASCADE`` — deleting a time dimension must leave the metric visibly incomplete
for somebody to fix, not quietly delete the metric along with it.

Purely additive. Nothing existing is altered, so a deployment that never creates a
semantic model behaves exactly as it did before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, plain JSON elsewhere (SQLite under the unit suite).
_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "semantic_models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("provider", sa.String(50), nullable=False, server_default="native"),
        sa.Column("provider_run_id", sa.String(255), nullable=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_semantic_models_slug"),
    )
    op.create_index(
        "ix_semantic_models_workspace_status", "semantic_models", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_semantic_models_provider_run", "semantic_models", ["provider", "provider_run_id"]
    )

    op.create_table(
        "semantic_datasets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("synonyms", _JSON, nullable=True),
        sa.Column(
            "catalog_id",
            UUID(as_uuid=True),
            sa.ForeignKey("catalogs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(255), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=False),
        sa.Column("primary_key", _JSON, nullable=True),
        sa.Column("validation_state", sa.String(16), nullable=False, server_default="unchecked"),
        sa.Column("validation_detail", sa.Text(), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("model_id", "name", name="uq_semantic_datasets_name"),
    )
    # "Which metrics depend on this table?" — the drop-table warning and the table
    # detail panel both ask in this direction.
    op.create_index(
        "ix_semantic_datasets_binding",
        "semantic_datasets",
        ["catalog_id", "schema_name", "table_name"],
    )

    op.create_table(
        "semantic_dimensions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("synonyms", _JSON, nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="categorical"),
        sa.Column("expr", sa.Text(), nullable=False),
        sa.Column("data_type", sa.String(64), nullable=True),
        sa.Column("time_grains", _JSON, nullable=True),
        sa.Column("is_default_time", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sample_values", _JSON, nullable=True),
        sa.Column("validation_state", sa.String(16), nullable=False, server_default="unchecked"),
        sa.Column("validation_detail", sa.Text(), nullable=True),
        sa.UniqueConstraint("model_id", "name", name="uq_semantic_dimensions_name"),
        sa.CheckConstraint(
            "kind IN ('categorical', 'time')",
            name="ck_semantic_dimensions_kind",
        ),
    )
    op.create_index("ix_semantic_dimensions_dataset", "semantic_dimensions", ["dataset_id"])

    op.create_table(
        "semantic_metrics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("synonyms", _JSON, nullable=True),
        sa.Column("agg", sa.String(20), nullable=False),
        sa.Column("expr", sa.Text(), nullable=True),
        sa.Column("filter", sa.Text(), nullable=True),
        # SET NULL, not CASCADE: losing the time axis must make the metric visibly
        # incomplete, not silently delete it.
        sa.Column(
            "time_dimension_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_dimensions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("caveat", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("validation_state", sa.String(16), nullable=False, server_default="unchecked"),
        sa.Column("validation_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("model_id", "name", name="uq_semantic_metrics_name"),
        sa.CheckConstraint(
            "agg IN ('sum', 'count', 'count_distinct', 'avg', 'min', 'max')",
            name="ck_semantic_metrics_agg",
        ),
    )
    op.create_index("ix_semantic_metrics_dataset", "semantic_metrics", ["dataset_id"])

    op.create_table(
        "semantic_relationships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "left_dataset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "right_dataset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("join_columns", _JSON, nullable=False),
        sa.Column("cardinality", sa.String(20), nullable=False, server_default="many_to_one"),
        sa.Column("validation_state", sa.String(16), nullable=False, server_default="unchecked"),
        sa.Column("validation_detail", sa.Text(), nullable=True),
        sa.UniqueConstraint("model_id", "name", name="uq_semantic_relationships_name"),
        # The fan-out guard. `one_to_many` is not a cardinality this system can
        # represent, because a join in that direction multiplies fact rows and
        # inflates every metric that crosses it.
        sa.CheckConstraint(
            "cardinality IN ('many_to_one', 'one_to_one')",
            name="ck_semantic_relationships_cardinality",
        ),
    )
    op.create_index("ix_semantic_relationships_left", "semantic_relationships", ["left_dataset_id"])


def downgrade() -> None:
    op.drop_index("ix_semantic_relationships_left", table_name="semantic_relationships")
    op.drop_table("semantic_relationships")
    op.drop_index("ix_semantic_metrics_dataset", table_name="semantic_metrics")
    op.drop_table("semantic_metrics")
    op.drop_index("ix_semantic_dimensions_dataset", table_name="semantic_dimensions")
    op.drop_table("semantic_dimensions")
    op.drop_index("ix_semantic_datasets_binding", table_name="semantic_datasets")
    op.drop_table("semantic_datasets")
    op.drop_index("ix_semantic_models_provider_run", table_name="semantic_models")
    op.drop_index("ix_semantic_models_workspace_status", table_name="semantic_models")
    op.drop_table("semantic_models")
