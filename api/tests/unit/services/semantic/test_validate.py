"""Validation: does this model still describe reality?

Every case here is a way a definition rots without anything erroring. A column
gets dropped, a table gets renamed, somebody declares a join on a column that is
not a key. The point of validation is that these become visible *before* they
become a wrong number, and that the resulting state is withheld from the
assistant rather than quietly used.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from api.models.semantic import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
)
from api.services.semantic.validate import (
    MAX_DATASETS_ADVISED,
    validate_model,
)
from tests.unit.conftest import seed_workspace

pytestmark = pytest.mark.asyncio

ORDER_COLUMNS = [
    {"id": 1, "name": "id", "type": "long", "required": True},
    {"id": 2, "name": "customer_id", "type": "long", "required": False},
    {"id": 3, "name": "total_amount", "type": "double", "required": False},
    {"id": 4, "name": "status", "type": "string", "required": False},
    {"id": 5, "name": "order_date", "type": "date", "required": False},
]
CUSTOMER_COLUMNS = [
    {"id": 1, "name": "id", "type": "long", "required": True},
    {"id": 2, "name": "country", "type": "string", "required": False},
    {"id": 3, "name": "email", "type": "string", "required": False},
]


async def _catalog_tables(fake_polaris, polaris_name: str):
    await fake_polaris.create_table(
        catalog=polaris_name, schema="analytics", name="orders", columns=ORDER_COLUMNS
    )
    await fake_polaris.create_table(
        catalog=polaris_name, schema="analytics", name="customers", columns=CUSTOMER_COLUMNS
    )


async def _build(db, workspace, catalog, *, with_relationship=True, right_key=("id",)):
    model = SemanticModel(workspace_id=workspace.id, slug="sales", name="Sales")
    db.add(model)
    await db.flush()

    orders = SemanticDataset(
        model_id=model.id,
        name="orders",
        catalog_id=catalog.id,
        schema_name="analytics",
        table_name="orders",
    )
    customers = SemanticDataset(
        model_id=model.id,
        name="customers",
        catalog_id=catalog.id,
        schema_name="analytics",
        table_name="customers",
        primary_key=list(right_key),
    )
    db.add_all([orders, customers])
    await db.flush()

    order_date = SemanticDimension(
        model_id=model.id,
        dataset_id=orders.id,
        name="order_date",
        kind="time",
        expr="order_date",
        time_grains=["day", "month"],
    )
    country = SemanticDimension(
        model_id=model.id,
        dataset_id=customers.id,
        name="country",
        kind="categorical",
        expr="country",
    )
    db.add_all([order_date, country])
    await db.flush()

    revenue = SemanticMetric(
        model_id=model.id,
        dataset_id=orders.id,
        name="revenue",
        agg="sum",
        expr="total_amount",
        filter="status <> 'test'",
        time_dimension_id=order_date.id,
    )
    db.add(revenue)

    if with_relationship:
        db.add(
            SemanticRelationship(
                model_id=model.id,
                name="orders_to_customers",
                left_dataset_id=orders.id,
                right_dataset_id=customers.id,
                join_columns=[{"left": "customer_id", "right": "id"}],
                cardinality="many_to_one",
            )
        )
    await db.commit()
    return model


async def _run(db, fake_polaris, model, catalog):
    return await validate_model(
        db, fake_polaris, model, catalog_names={catalog.id: catalog.polaris_name}
    )


async def test_a_sound_model_validates(db_session, fake_polaris):

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    report = await _run(db_session, fake_polaris, model, catalog)

    assert report.ok
    assert report.errors == []
    states = (
        (
            await db_session.execute(
                select(SemanticDataset.validation_state).where(SemanticDataset.model_id == model.id)
            )
        )
        .scalars()
        .all()
    )
    assert set(states) == {"ok"}


async def test_a_missing_table_breaks_its_dataset(db_session, fake_polaris):

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    fake_polaris.tables.pop((catalog.polaris_name, "analytics", "orders"))

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    assert any(e["name"] == "orders" for e in report.errors)


async def test_a_dropped_column_breaks_the_metric_that_reads_it(db_session, fake_polaris):
    """The core rot case: the definition is unchanged, the table is not."""

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    without_amount = [c for c in ORDER_COLUMNS if c["name"] != "total_amount"]
    fake_polaris.tables.pop((catalog.polaris_name, "analytics", "orders"))
    await fake_polaris.create_table(
        catalog=catalog.polaris_name, schema="analytics", name="orders", columns=without_amount
    )

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    failure = next(e for e in report.errors if e["kind"] == "metric")
    assert "total_amount" in failure["detail"]

    metric = (
        await db_session.execute(select(SemanticMetric).where(SemanticMetric.name == "revenue"))
    ).scalar_one()
    assert metric.validation_state == "broken"


async def test_a_column_missing_only_from_the_filter_still_breaks_the_metric(
    db_session, fake_polaris
):

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    without_status = [c for c in ORDER_COLUMNS if c["name"] != "status"]
    fake_polaris.tables.pop((catalog.polaris_name, "analytics", "orders"))
    await fake_polaris.create_table(
        catalog=catalog.polaris_name, schema="analytics", name="orders", columns=without_status
    )

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    assert any("status" in e["detail"] for e in report.errors)


async def test_a_join_on_a_non_key_column_is_rejected(db_session, fake_polaris):
    """The fan-out check.

    Joining ``customers`` on ``email`` when its key is ``id`` does not guarantee
    one match per order. Every order with a duplicated email would be counted
    twice, inflating revenue with no error anywhere.
    """

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    rel = (await db_session.execute(select(SemanticRelationship))).scalar_one()
    rel.join_columns = [{"left": "customer_id", "right": "email"}]
    await db_session.commit()

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    failure = next(e for e in report.errors if e["kind"] == "relationship")
    assert "primary key" in failure["detail"]
    assert "multiply rows" in failure["detail"]


async def test_a_right_side_without_a_key_cannot_be_joined_to(db_session, fake_polaris):

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog, right_key=())

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    failure = next(e for e in report.errors if e["kind"] == "relationship")
    assert "no primary key" in failure["detail"]


async def test_a_missing_join_column_is_reported(db_session, fake_polaris):

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    rel = (await db_session.execute(select(SemanticRelationship))).scalar_one()
    rel.join_columns = [{"left": "buyer_id", "right": "id"}]
    await db_session.commit()

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    assert any("buyer_id" in e["detail"] for e in report.errors)


async def test_an_unparseable_expression_is_reported(db_session, fake_polaris):

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    metric = (
        await db_session.execute(select(SemanticMetric).where(SemanticMetric.name == "revenue"))
    ).scalar_one()
    metric.expr = "SUM((("
    await db_session.commit()

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    assert any("not a valid scalar SQL expression" in e["detail"] for e in report.errors)


async def test_a_metric_with_no_time_binding_is_a_warning_not_an_error(db_session, fake_polaris):
    """Timeless metrics are legitimate; an unbound axis is still worth saying."""

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    metric = (
        await db_session.execute(select(SemanticMetric).where(SemanticMetric.name == "revenue"))
    ).scalar_one()
    metric.time_dimension_id = None
    await db_session.commit()

    report = await _run(db_session, fake_polaris, model, catalog)

    assert report.ok
    assert any("not bound to a time dimension" in w for w in report.warnings)


async def test_an_unsupported_grain_breaks_the_dimension(db_session, fake_polaris):

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    dim = (
        await db_session.execute(
            select(SemanticDimension).where(SemanticDimension.name == "order_date")
        )
    ).scalar_one()
    dim.time_grains = ["fortnight"]
    await db_session.commit()

    report = await _run(db_session, fake_polaris, model, catalog)

    assert not report.ok
    assert any("fortnight" in e["detail"] for e in report.errors)


async def test_a_large_model_warns_rather_than_failing(db_session, fake_polaris):
    """Splitting is the fix, and only an author can do it."""

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog, with_relationship=False)

    for i in range(MAX_DATASETS_ADVISED):
        name = f"extra_{i}"
        await fake_polaris.create_table(
            catalog=catalog.polaris_name, schema="analytics", name=name, columns=ORDER_COLUMNS
        )
        db_session.add(
            SemanticDataset(
                model_id=model.id,
                name=name,
                catalog_id=catalog.id,
                schema_name="analytics",
                table_name=name,
            )
        )
    await db_session.commit()

    report = await _run(db_session, fake_polaris, model, catalog)

    assert report.ok
    assert any("splitting it" in w for w in report.warnings)


async def test_revalidation_clears_a_previous_failure(db_session, fake_polaris):
    """A repaired definition must come back, not stay broken forever."""

    ws, catalog = await seed_workspace(db_session, user_id=uuid.uuid4())
    await _catalog_tables(fake_polaris, catalog.polaris_name)
    model = await _build(db_session, ws, catalog)

    fake_polaris.tables.pop((catalog.polaris_name, "analytics", "orders"))
    assert not (await _run(db_session, fake_polaris, model, catalog)).ok

    await fake_polaris.create_table(
        catalog=catalog.polaris_name, schema="analytics", name="orders", columns=ORDER_COLUMNS
    )

    assert (await _run(db_session, fake_polaris, model, catalog)).ok
