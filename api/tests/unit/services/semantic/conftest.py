"""A small star schema, built in memory.

``orders`` is the fact table; ``customers`` and ``products`` hang off it as
lookups. That is enough shape to exercise every rule that matters: a one-hop
join, a two-hop join, a dataset reachable two different ways, and a fan-out
attempt.

Built as plain dataclasses rather than through the database on purpose. The
compiler is a pure function and its tests should be able to say so.
"""

from __future__ import annotations

import uuid

import pytest

from api.services.semantic.model import (
    Dataset,
    Dimension,
    LoadedModel,
    Metric,
    Relationship,
)


def make_dataset(name: str, *, table: str | None = None, primary_key=()) -> Dataset:
    return Dataset(
        id=uuid.uuid4(),
        name=name,
        description=None,
        synonyms=(),
        catalog_id=uuid.uuid4(),
        catalog_slug="warehouse",
        schema_name="analytics",
        table_name=table or name,
        primary_key=tuple(primary_key),
        validation_state="ok",
    )


def make_dimension(
    name: str,
    dataset: str,
    *,
    kind: str = "categorical",
    expr: str | None = None,
    synonyms=(),
    time_grains=("day", "week", "month", "quarter", "year"),
    is_default_time: bool = False,
    sample_values=(),
    description: str | None = None,
    display_name: str | None = None,
) -> Dimension:
    return Dimension(
        id=uuid.uuid4(),
        name=name,
        display_name=display_name,
        description=description,
        synonyms=tuple(synonyms),
        kind=kind,
        expr=expr or name,
        data_type=None,
        time_grains=tuple(time_grains) if kind == "time" else (),
        is_default_time=is_default_time,
        sample_values=tuple(sample_values),
        dataset=dataset,
        validation_state="ok",
    )


def make_metric(
    name: str,
    dataset: str,
    *,
    agg: str = "sum",
    expr: str | None = "total_amount",
    filter: str | None = None,
    time_dimension: str | None = "order_date",
    synonyms=(),
    caveat: str | None = None,
    status: str = "published",
    description: str | None = None,
    display_name: str | None = None,
) -> Metric:
    return Metric(
        id=uuid.uuid4(),
        name=name,
        display_name=display_name,
        description=description,
        synonyms=tuple(synonyms),
        agg=agg,
        expr=expr,
        filter=filter,
        time_dimension=time_dimension,
        caveat=caveat,
        status=status,
        dataset=dataset,
        validation_state="ok",
    )


def make_relationship(
    name: str, left: str, right: str, *, columns=(("customer_id", "id"),), cardinality="many_to_one"
) -> Relationship:
    return Relationship(
        id=uuid.uuid4(),
        name=name,
        left=left,
        right=right,
        join_columns=tuple(columns),
        cardinality=cardinality,
    )


def make_model(
    *,
    datasets=None,
    dimensions=None,
    metrics=None,
    relationships=(),
    slug: str = "sales",
    status: str = "published",
) -> LoadedModel:
    return LoadedModel(
        id=uuid.uuid4(),
        slug=slug,
        name=slug.title(),
        description=None,
        status=status,
        provider="native",
        datasets={d.name: d for d in (datasets or [])},
        dimensions={d.name: d for d in (dimensions or [])},
        metrics={m.name: m for m in (metrics or [])},
        relationships=tuple(relationships),
    )


@pytest.fixture
def star() -> LoadedModel:
    """orders -> customers, orders -> products. One hop each, no ambiguity."""
    return make_model(
        datasets=[
            make_dataset("orders"),
            make_dataset("customers", primary_key=("id",)),
            make_dataset("products", primary_key=("id",)),
        ],
        dimensions=[
            make_dimension("order_date", "orders", kind="time", is_default_time=True),
            make_dimension(
                "created_at",
                "orders",
                kind="time",
                description="When the row was written, not when the order happened.",
            ),
            make_dimension(
                "country",
                "customers",
                synonyms=("nation", "market"),
                sample_values=("United States", "Canada"),
                display_name="Country",
            ),
            make_dimension("segment", "customers", synonyms=("customer segment",)),
            make_dimension("category", "products", synonyms=("product category",)),
        ],
        metrics=[
            make_metric(
                "revenue",
                "orders",
                synonyms=("turnover", "gmv"),
                filter="status <> 'test'",
                caveat="Excludes internal test orders.",
                display_name="Revenue",
                description="Net booked revenue.",
            ),
            make_metric(
                "order_count",
                "orders",
                agg="count",
                expr=None,
                synonyms=("orders",),
            ),
            make_metric(
                "unique_customers",
                "orders",
                agg="count_distinct",
                expr="customer_id",
                synonyms=("distinct customers",),
            ),
        ],
        relationships=[
            make_relationship("orders_to_customers", "orders", "customers"),
            make_relationship(
                "orders_to_products", "orders", "products", columns=(("product_id", "id"),)
            ),
        ],
    )
