"""The semantic layer against real Postgres and real Polaris.

Covers what the SQLite unit suite cannot. Validation is the whole point of this
file: it resolves every binding against the *live* catalog rather than a cached
copy, so the only way to know it does that correctly is to give it a real
catalog, remove a real column, and check that the definition goes broken.

Also exercises the schema on the real backend — the JSONB columns, the check
constraints, and the unique keys all behave differently on Postgres than they do
on SQLite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from api.models.semantic import SemanticDataset, SemanticMetric, SemanticModel

pytestmark = pytest.mark.integration

ORDER_COLUMNS = [
    {"name": "id", "type": "BIGINT"},
    {"name": "customer_id", "type": "BIGINT"},
    {"name": "total_amount", "type": "DOUBLE"},
    {"name": "status", "type": "VARCHAR"},
    {"name": "order_date", "type": "DATE"},
]
CUSTOMER_COLUMNS = [
    {"name": "id", "type": "BIGINT"},
    {"name": "country", "type": "VARCHAR"},
]


async def _workspace(admin_client, workspace_factory) -> tuple[str, str]:
    slug = f"dh-sem-{uuid4().hex[:8]}"
    await workspace_factory(slug=slug, name="Semantic")
    catalog = f"c_{slug.replace('-', '_')}"
    created = await admin_client.post(f"/workspaces/{slug}/catalogs", json={"name": catalog})
    assert created.status_code == 201, created.text
    await admin_client.post(
        f"/workspaces/{slug}/catalogs/{catalog}/schemas", json={"name": "analytics"}
    )
    for name, columns in (("orders", ORDER_COLUMNS), ("customers", CUSTOMER_COLUMNS)):
        made = await admin_client.post(
            f"/workspaces/{slug}/catalogs/{catalog}/schemas/analytics/tables",
            json={"name": name, "columns": columns},
        )
        assert made.status_code == 201, made.text
    return slug, catalog


async def _model(admin_client, ws: str, catalog: str) -> None:
    base = f"/workspaces/{ws}/semantic/models"
    assert (
        await admin_client.post(base, json={"slug": "sales", "name": "Sales"})
    ).status_code == 201
    model = f"{base}/sales"
    for name, key in (("orders", ["id"]), ("customers", ["id"])):
        made = await admin_client.post(
            f"{model}/datasets",
            json={
                "name": name,
                "catalog": catalog,
                "schema_name": "analytics",
                "table_name": name,
                "primary_key": key,
            },
        )
        assert made.status_code == 201, made.text
    assert (
        await admin_client.post(
            f"{model}/relationships",
            json={
                "name": "orders_to_customers",
                "left_dataset": "orders",
                "right_dataset": "customers",
                "join_columns": [{"left": "customer_id", "right": "id"}],
            },
        )
    ).status_code == 201
    assert (
        await admin_client.post(
            f"{model}/dimensions",
            json={
                "name": "order_date",
                "dataset": "orders",
                "kind": "time",
                "is_default_time": True,
            },
        )
    ).status_code == 201
    assert (
        await admin_client.post(
            f"{model}/dimensions",
            json={"name": "country", "dataset": "customers", "synonyms": ["nation"]},
        )
    ).status_code == 201
    assert (
        await admin_client.post(
            f"{model}/metrics",
            json={
                "name": "revenue",
                "dataset": "orders",
                "agg": "sum",
                "expr": "total_amount",
                "filter": "status <> 'test'",
                "time_dimension": "order_date",
                "synonyms": ["turnover"],
            },
        )
    ).status_code == 201


async def test_a_model_validates_against_a_real_catalog(admin_client, workspace_factory):
    ws, catalog = await _workspace(admin_client, workspace_factory)
    await _model(admin_client, ws, catalog)

    report = await admin_client.post(f"/workspaces/{ws}/semantic/models/sales/validate")

    assert report.status_code == 200, report.text
    assert report.json()["ok"] is True


async def test_dropping_a_table_breaks_its_definitions_rather_than_deleting_them(
    admin_client, workspace_factory, db_session
):
    """The behaviour that separates this from grants and lineage.

    Those are deleted when a table goes, because they describe the table. A
    metric describes the business and outlives it — so it must survive, visibly
    broken and repairable, rather than vanishing and quietly returning the
    assistant to inventing its own revenue calculation.
    """
    ws, catalog = await _workspace(admin_client, workspace_factory)
    await _model(admin_client, ws, catalog)

    dropped = await admin_client.delete(
        f"/workspaces/{ws}/catalogs/{catalog}/schemas/analytics/tables/orders"
    )
    assert dropped.status_code == 204, dropped.text

    body = (await admin_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    orders = next(d for d in body["datasets"] if d["name"] == "orders")
    assert orders["validation_state"] == "broken"
    assert "dropped" in (orders["validation_detail"] or "")

    # Still there, not deleted.
    surviving = (
        await db_session.execute(sa.select(sa.func.count()).select_from(SemanticMetric))
    ).scalar_one()
    assert surviving >= 1


async def test_a_removed_column_breaks_the_metric_that_reads_it(
    admin_client, workspace_factory, polaris
):
    """The rot case, against a real catalog: the definition is unchanged, the
    table is not."""
    ws, catalog = await _workspace(admin_client, workspace_factory)
    await _model(admin_client, ws, catalog)
    assert (await admin_client.post(f"/workspaces/{ws}/semantic/models/sales/validate")).json()[
        "ok"
    ] is True

    # Recreate `orders` without the column revenue sums.
    await admin_client.delete(
        f"/workspaces/{ws}/catalogs/{catalog}/schemas/analytics/tables/orders"
    )
    await admin_client.post(
        f"/workspaces/{ws}/catalogs/{catalog}/schemas/analytics/tables",
        json={
            "name": "orders",
            "columns": [c for c in ORDER_COLUMNS if c["name"] != "total_amount"],
        },
    )

    report = (await admin_client.post(f"/workspaces/{ws}/semantic/models/sales/validate")).json()

    assert report["ok"] is False
    assert any("total_amount" in e["detail"] for e in report["errors"])


async def test_publishing_is_refused_while_anything_is_broken(admin_client, workspace_factory):
    ws, catalog = await _workspace(admin_client, workspace_factory)
    await _model(admin_client, ws, catalog)
    await admin_client.post(
        f"/workspaces/{ws}/semantic/models/sales/metrics",
        json={"name": "bogus", "dataset": "orders", "agg": "sum", "expr": "nope"},
    )

    refused = await admin_client.post(f"/workspaces/{ws}/semantic/models/sales/publish")

    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "validation_failed"


async def test_compiled_sql_names_the_real_catalog(admin_client, workspace_factory):
    """So the statement meets the same grant check as any hand-written query."""
    ws, catalog = await _workspace(admin_client, workspace_factory)
    await _model(admin_client, ws, catalog)
    await admin_client.patch(
        f"/workspaces/{ws}/semantic/models/sales/metrics/revenue",
        json={"status": "published"},
    )
    await admin_client.post(f"/workspaces/{ws}/semantic/models/sales/publish")

    compiled = (
        await admin_client.post(
            f"/workspaces/{ws}/semantic/compile",
            json={"model": "sales", "metrics": ["revenue"], "dimensions": ["country"]},
        )
    ).json()

    assert f"{catalog}.analytics.orders" in compiled["sql"]
    assert f"{catalog}.analytics.customers" in compiled["sql"]
    assert "LEFT JOIN" in compiled["sql"]


async def test_jsonb_columns_round_trip_on_postgres(admin_client, workspace_factory, db_session):
    ws, catalog = await _workspace(admin_client, workspace_factory)
    await _model(admin_client, ws, catalog)

    dataset = (
        (
            await db_session.execute(
                sa.select(SemanticDataset).where(SemanticDataset.name == "customers")
            )
        )
        .scalars()
        .first()
    )
    metric = (
        (
            await db_session.execute(
                sa.select(SemanticMetric).where(SemanticMetric.name == "revenue")
            )
        )
        .scalars()
        .first()
    )

    assert dataset is not None and dataset.primary_key == ["id"]
    assert metric is not None and metric.synonyms == ["turnover"]


async def test_the_slug_unique_constraint_holds_on_postgres(
    admin_client, workspace_factory, db_session
):
    ws, _ = await _workspace(admin_client, workspace_factory)
    assert (
        await admin_client.post(
            f"/workspaces/{ws}/semantic/models", json={"slug": "sales", "name": "Sales"}
        )
    ).status_code == 201

    again = await admin_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "sales", "name": "Other"}
    )

    assert again.status_code == 409
    count = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticModel)
            .where(SemanticModel.slug == "sales")
        )
    ).scalar_one()
    assert count == 1


async def test_a_workspace_cannot_see_another_workspaces_models(admin_client, workspace_factory):
    ws_a, catalog_a = await _workspace(admin_client, workspace_factory)
    await _model(admin_client, ws_a, catalog_a)
    ws_b, _ = await _workspace(admin_client, workspace_factory)

    found = await admin_client.get(f"/workspaces/{ws_b}/semantic/models/sales")

    assert found.status_code == 404
    assert (await admin_client.get(f"/workspaces/{ws_b}/semantic/models")).json() == []
