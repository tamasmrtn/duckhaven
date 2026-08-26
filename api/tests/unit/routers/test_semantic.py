"""The semantic layer API: authoring, publishing, search and compile.

The access model is the thing worth pinning here. Reading is ``reader``,
authoring is ``writer``, and **publishing is ``owner``** — because publishing is
what turns somebody's draft into an answer the assistant will give, and the
minimum useful governance is exactly the power to stop that happening by accident.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.auth import hash_password

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


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="owner@s.local", password_hash=hash_password("pw"), name="Owner", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@s.local", "password": "pw"})
    return client


@pytest.fixture
async def ws(auth_client, owner, db_session):
    """A workspace with one catalog holding an orders/customers star."""
    resp = await auth_client.post("/workspaces", json={"slug": "sem", "name": "Sem"})
    slug = resp.json()["slug"]
    backend = StorageBackend(kind="object_store", name="p", root_uri="", created_by=owner.id)
    db_session.add(backend)
    await db_session.commit()
    await db_session.refresh(backend)
    await auth_client.post(
        f"/workspaces/{slug}/catalogs",
        json={"name": "warehouse", "storage_backend_id": str(backend.id)},
    )
    await auth_client.post(
        f"/workspaces/{slug}/catalogs/warehouse/schemas", json={"name": "analytics"}
    )
    for name, columns in (("orders", ORDER_COLUMNS), ("customers", CUSTOMER_COLUMNS)):
        await auth_client.post(
            f"/workspaces/{slug}/catalogs/warehouse/schemas/analytics/tables",
            json={"name": name, "columns": columns},
        )
    return slug


async def build_model(client: AsyncClient, ws: str, *, slug: str = "sales") -> None:
    """A minimal but complete model: two datasets, a join, a time axis, a metric."""
    await client.post(f"/workspaces/{ws}/semantic/models", json={"slug": slug, "name": "Sales"})
    base = f"/workspaces/{ws}/semantic/models/{slug}"
    await client.post(
        f"{base}/datasets",
        json={
            "name": "orders",
            "catalog": "warehouse",
            "schema_name": "analytics",
            "table_name": "orders",
            "primary_key": ["id"],
        },
    )
    await client.post(
        f"{base}/datasets",
        json={
            "name": "customers",
            "catalog": "warehouse",
            "schema_name": "analytics",
            "table_name": "customers",
            "primary_key": ["id"],
        },
    )
    await client.post(
        f"{base}/relationships",
        json={
            "name": "orders_to_customers",
            "left_dataset": "orders",
            "right_dataset": "customers",
            "join_columns": [{"left": "customer_id", "right": "id"}],
        },
    )
    await client.post(
        f"{base}/dimensions",
        json={"name": "order_date", "dataset": "orders", "kind": "time", "is_default_time": True},
    )
    await client.post(
        f"{base}/dimensions",
        json={"name": "country", "dataset": "customers", "synonyms": ["nation"]},
    )
    await client.post(
        f"{base}/metrics",
        json={
            "name": "revenue",
            "dataset": "orders",
            "agg": "sum",
            "expr": "total_amount",
            "filter": "status <> 'test'",
            "time_dimension": "order_date",
            "synonyms": ["turnover"],
            "caveat": "Excludes internal test orders.",
        },
    )


# ── Authoring ─────────────────────────────────────────────────────────────────


async def test_a_new_model_starts_as_a_draft(auth_client, ws):
    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "sales", "name": "Sales"}
    )

    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"
    assert resp.json()["provider"] == "native"


async def test_duplicate_slugs_conflict(auth_client, ws):
    await auth_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "sales", "name": "Sales"}
    )
    again = await auth_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "sales", "name": "Other"}
    )

    assert again.status_code == 409


async def test_a_name_that_is_not_an_identifier_is_rejected(auth_client, ws):
    """Names become SQL aliases, so a name needing quotes is a name to reject."""
    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "Sales Model!", "name": "x"}
    )

    assert resp.status_code == 422


async def test_the_full_model_reads_back(auth_client, ws):
    await build_model(auth_client, ws)

    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()

    assert {d["name"] for d in body["datasets"]} == {"orders", "customers"}
    assert {d["name"] for d in body["dimensions"]} == {"order_date", "country"}
    assert [m["name"] for m in body["metrics"]] == ["revenue"]
    assert body["relationships"][0]["cardinality"] == "many_to_one"


async def test_a_metric_reads_back_with_a_readable_calculation(auth_client, ws):
    """ "How is this computed?" must have an answer that is not two raw fields."""
    await build_model(auth_client, ws)

    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    metric = body["metrics"][0]

    assert metric["expression"] == "SUM(total_amount) FILTER (WHERE status <> 'test')"
    assert metric["time_dimension"] == "order_date"


async def test_a_metric_cannot_be_measured_on_a_categorical_dimension(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/models/sales/metrics",
        json={
            "name": "bad",
            "dataset": "orders",
            "agg": "sum",
            "expr": "total_amount",
            "time_dimension": "country",
        },
    )

    assert resp.status_code == 422
    assert "not a time dimension" in str(resp.json()["message"])


async def test_a_sum_without_an_expression_is_rejected(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/models/sales/metrics",
        json={"name": "bad", "dataset": "orders", "agg": "sum"},
    )

    assert resp.status_code == 422


async def test_a_dimension_on_an_unknown_dataset_is_rejected(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/models/sales/dimensions",
        json={"name": "channel", "dataset": "marketing"},
    )

    assert resp.status_code == 422


async def test_a_fan_out_cardinality_is_rejected_at_the_edge(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/models/sales/relationships",
        json={
            "name": "bad",
            "left_dataset": "customers",
            "right_dataset": "orders",
            "join_columns": [{"left": "id", "right": "customer_id"}],
            "cardinality": "one_to_many",
        },
    )

    assert resp.status_code == 422


async def test_editing_a_metric_invalidates_its_previous_verdict(auth_client, ws):
    """An edited expression has not been checked, whatever the old answer was."""
    await build_model(auth_client, ws)
    await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/validate")

    resp = await auth_client.patch(
        f"/workspaces/{ws}/semantic/models/sales/metrics/revenue",
        json={"expr": "total_amount * 2"},
    )

    assert resp.status_code == 200
    assert resp.json()["validation_state"] == "unchecked"


# ── Validation and publishing ─────────────────────────────────────────────────


async def test_a_sound_model_validates(auth_client, ws):
    await build_model(auth_client, ws)

    body = (await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/validate")).json()

    assert body["ok"] is True
    assert body["errors"] == []


async def test_validation_catches_a_column_that_does_not_exist(auth_client, ws):
    await build_model(auth_client, ws)
    await auth_client.post(
        f"/workspaces/{ws}/semantic/models/sales/metrics",
        json={"name": "bogus", "dataset": "orders", "agg": "sum", "expr": "not_a_column"},
    )

    body = (await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/validate")).json()

    assert body["ok"] is False
    assert any("not_a_column" in e["detail"] for e in body["errors"])


async def test_publishing_requires_a_model_that_validates(auth_client, ws):
    """The moment a draft becomes quotable is the moment to insist it holds."""
    await build_model(auth_client, ws)
    await auth_client.post(
        f"/workspaces/{ws}/semantic/models/sales/metrics",
        json={"name": "bogus", "dataset": "orders", "agg": "sum", "expr": "not_a_column"},
    )

    resp = await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/publish")

    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_failed"


async def test_publishing_a_sound_model_works(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/publish")

    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


# ── Search ────────────────────────────────────────────────────────────────────


async def test_search_finds_a_metric_by_synonym(auth_client, ws):
    await _published(auth_client, ws)

    body = (
        await auth_client.get(f"/workspaces/{ws}/semantic/search", params={"q": "our turnover"})
    ).json()

    assert body["hits"][0]["name"] == "revenue"


async def test_search_ignores_unpublished_models_by_default(auth_client, ws):
    """A draft must not answer a question as though it were settled."""
    await build_model(auth_client, ws)

    body = (
        await auth_client.get(f"/workspaces/{ws}/semantic/search", params={"q": "revenue"})
    ).json()

    assert body["hits"] == []


# ── Compile ───────────────────────────────────────────────────────────────────


async def _published(auth_client, ws):
    await build_model(auth_client, ws)
    await auth_client.patch(
        f"/workspaces/{ws}/semantic/models/sales/metrics/revenue", json={"status": "published"}
    )
    await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/publish")


async def test_compile_returns_sql_and_does_not_execute(auth_client, ws):
    await _published(auth_client, ws)

    body = (
        await auth_client.post(
            f"/workspaces/{ws}/semantic/compile",
            json={"model": "sales", "metrics": ["revenue"], "dimensions": ["country"]},
        )
    ).json()

    assert "SUM(orders.total_amount)" in body["sql"]
    assert "LEFT JOIN" in body["sql"]
    assert "warehouse.analytics.orders" in body["sql"]
    # No query id anywhere: compiling is not running.
    assert "query_id" not in body


async def test_compile_reports_the_definitions_it_used(auth_client, ws):
    await _published(auth_client, ws)

    body = (
        await auth_client.post(
            f"/workspaces/{ws}/semantic/compile",
            json={"model": "sales", "metrics": ["revenue"]},
        )
    ).json()

    assert any(d["kind"] == "metric" and d["name"] == "revenue" for d in body["definitions_used"])
    assert any("test orders" in w for w in body["warnings"])


async def test_compile_refuses_an_unknown_metric_and_names_the_real_ones(auth_client, ws):
    await _published(auth_client, ws)

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/compile", json={"model": "sales", "metrics": ["profit"]}
    )

    assert resp.status_code == 422
    assert "revenue" in resp.json()["message"]


async def test_compile_refuses_an_unpublished_model(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/compile", json={"model": "sales", "metrics": ["revenue"]}
    )

    assert resp.status_code == 404


async def test_compile_applies_a_time_window(auth_client, ws):
    await _published(auth_client, ws)

    body = (
        await auth_client.post(
            f"/workspaces/{ws}/semantic/compile",
            json={
                "model": "sales",
                "metrics": ["revenue"],
                "grain": "month",
                "time_range": {"kind": "last_complete", "grain": "month", "n": 3},
            },
        )
    ).json()

    assert "DATE_TRUNC('MONTH', orders.order_date)" in body["sql"]
    assert "orders.order_date >=" in body["sql"]


async def test_a_time_window_without_a_grain_is_rejected(auth_client, ws):
    await _published(auth_client, ws)

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/compile",
        json={
            "model": "sales",
            "metrics": ["revenue"],
            "time_range": {"kind": "trailing", "n": 30},
        },
    )

    assert resp.status_code == 422


async def test_legal_dimensions_are_listed(auth_client, ws):
    await _published(auth_client, ws)

    body = (
        await auth_client.get(f"/workspaces/{ws}/semantic/models/sales/metrics/revenue/dimensions")
    ).json()

    assert set(body) == {"order_date", "country"}


# ── Impact ────────────────────────────────────────────────────────────────────


async def test_the_table_page_can_ask_what_depends_on_it(auth_client, ws):
    await _published(auth_client, ws)

    body = (
        await auth_client.get(
            f"/workspaces/{ws}/catalogs/warehouse/schemas/analytics/tables/orders/semantic"
        )
    ).json()

    names = {d["name"] for d in body["dependents"]}
    assert "revenue" in names
    revenue = next(d for d in body["dependents"] if d["name"] == "revenue")
    assert set(revenue["columns"]) == {"total_amount", "status"}


async def test_dependents_can_be_narrowed_to_one_column(auth_client, ws):
    """The question asked just before somebody drops a column."""
    await _published(auth_client, ws)

    body = (
        await auth_client.get(
            f"/workspaces/{ws}/catalogs/warehouse/schemas/analytics/tables/orders/semantic",
            params={"column": "total_amount"},
        )
    ).json()

    assert {d["name"] for d in body["dependents"]} == {"revenue"}


async def test_a_table_nothing_depends_on_reports_nothing(auth_client, ws):
    await _published(auth_client, ws)

    body = (
        await auth_client.get(
            f"/workspaces/{ws}/catalogs/warehouse/schemas/analytics/tables/orders/semantic",
            params={"column": "id"},
        )
    ).json()

    assert body["dependents"] == []


# ── Authorization ─────────────────────────────────────────────────────────────


@pytest.fixture
async def reader(db_session, auth_client, ws) -> User:
    import sqlalchemy as sa

    from api.models.workspace import Workspace

    u = User(email="reader@s.local", password_hash=hash_password("pw"), name="R", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    workspace = (
        await db_session.execute(sa.select(Workspace).where(Workspace.slug == ws))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=u.id, role="reader"))
    await db_session.commit()
    return u


async def test_a_reader_cannot_author(client, auth_client, ws, reader):
    await build_model(auth_client, ws)
    await client.post("/auth/login", json={"email": "reader@s.local", "password": "pw"})

    resp = await client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "other", "name": "Other"}
    )

    assert resp.status_code == 403


async def test_a_reader_can_read(client, auth_client, ws, reader):
    await build_model(auth_client, ws)
    await client.post("/auth/login", json={"email": "reader@s.local", "password": "pw"})

    resp = await client.get(f"/workspaces/{ws}/semantic/models/sales")

    assert resp.status_code == 200


async def test_a_writer_cannot_publish(client, auth_client, ws, db_session):
    """Publishing is what makes a definition authoritative, so it is an owner act."""
    import sqlalchemy as sa

    from api.models.workspace import Workspace

    await build_model(auth_client, ws)
    writer = User(email="writer@s.local", password_hash=hash_password("pw"), name="W", role="user")
    db_session.add(writer)
    await db_session.commit()
    await db_session.refresh(writer)
    workspace = (
        await db_session.execute(sa.select(Workspace).where(Workspace.slug == ws))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=writer.id, role="writer"))
    await db_session.commit()

    await client.post("/auth/login", json={"email": "writer@s.local", "password": "pw"})
    resp = await client.post(f"/workspaces/{ws}/semantic/models/sales/publish")

    assert resp.status_code == 403


async def test_a_non_member_sees_nothing(client, auth_client, ws, db_session):
    await build_model(auth_client, ws)
    stranger = User(
        email="stranger@s.local", password_hash=hash_password("pw"), name="S", role="user"
    )
    db_session.add(stranger)
    await db_session.commit()

    await client.post("/auth/login", json={"email": "stranger@s.local", "password": "pw"})
    resp = await client.get(f"/workspaces/{ws}/semantic/models/sales")

    assert resp.status_code == 403


async def test_an_unknown_model_is_a_404(auth_client, ws):
    resp = await auth_client.get(f"/workspaces/{ws}/semantic/models/nope")

    assert resp.status_code == 404


# ── Removing children ─────────────────────────────────────────────────────────
#
# Deleting a definition is how a mistake gets fixed, so it carries no more
# ceremony than adding one. The interesting cases are the two where a naive
# delete would destroy something the caller did not ask to destroy.


async def test_a_metric_can_be_deleted(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.delete(f"/workspaces/{ws}/semantic/models/sales/metrics/revenue")

    assert resp.status_code == 204
    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    assert body["metrics"] == []


async def test_a_relationship_can_be_deleted(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.delete(
        f"/workspaces/{ws}/semantic/models/sales/relationships/orders_to_customers"
    )

    assert resp.status_code == 204
    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    assert body["relationships"] == []


async def test_deleting_a_time_axis_is_refused_while_a_metric_is_measured_on_it(auth_client, ws):
    """Clearing the binding would be a silent re-measurement, not a clean break.

    A metric whose axis is merely absent looks exactly like one that never had an
    axis, and the compiler answers that kind using the dataset's default date —
    so allowing this would start measuring revenue on `created_at` and report
    nothing. Refusing keeps the ambiguous state unreachable, and the metric is
    never deleted as a side effect either way.
    """
    await build_model(auth_client, ws)

    resp = await auth_client.delete(f"/workspaces/{ws}/semantic/models/sales/dimensions/order_date")

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "dimension_in_use"
    assert body["details"]["dependents"] == ["metric 'revenue'"]
    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    assert {d["name"] for d in body["dimensions"]} == {"order_date", "country"}


async def test_a_time_axis_can_be_deleted_once_the_metric_is_rebound(auth_client, ws):
    """The refusal names the way out, so the way out has to work."""
    await build_model(auth_client, ws)
    base = f"/workspaces/{ws}/semantic/models/sales"
    await auth_client.post(
        f"{base}/dimensions",
        json={"name": "shipped_at", "dataset": "orders", "kind": "time"},
    )
    await auth_client.patch(f"{base}/metrics/revenue", json={"time_dimension": "shipped_at"})

    resp = await auth_client.delete(f"{base}/dimensions/order_date")

    assert resp.status_code == 204
    body = (await auth_client.get(base)).json()
    assert body["metrics"][0]["time_dimension"] == "shipped_at"


async def test_a_dimension_nothing_measures_on_deletes_cleanly(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.delete(f"/workspaces/{ws}/semantic/models/sales/dimensions/country")

    assert resp.status_code == 204
    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    assert {d["name"] for d in body["dimensions"]} == {"order_date"}


async def test_deleting_a_dataset_is_refused_while_anything_binds_it(auth_client, ws):
    """The FKs cascade, so an allowed delete here would take metrics with it."""
    await build_model(auth_client, ws)

    resp = await auth_client.delete(f"/workspaces/{ws}/semantic/models/sales/datasets/orders")

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "dataset_in_use"
    # Naming them is what makes the refusal actionable rather than annoying.
    dependents = body["details"]["dependents"]
    assert "metric 'revenue'" in dependents
    assert "dimension 'order_date'" in dependents
    assert "relationship 'orders_to_customers'" in dependents
    # Nothing was removed on the way to refusing.
    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    assert len(body["metrics"]) == 1


async def test_a_dataset_can_be_deleted_once_nothing_binds_it(auth_client, ws):
    await build_model(auth_client, ws)
    base = f"/workspaces/{ws}/semantic/models/sales"
    await auth_client.delete(f"{base}/relationships/orders_to_customers")
    await auth_client.delete(f"{base}/dimensions/country")

    resp = await auth_client.delete(f"{base}/datasets/customers")

    assert resp.status_code == 204
    body = (await auth_client.get(base)).json()
    assert {d["name"] for d in body["datasets"]} == {"orders"}


async def test_deleting_something_that_is_not_there_is_a_404(auth_client, ws):
    await build_model(auth_client, ws)

    resp = await auth_client.delete(f"/workspaces/{ws}/semantic/models/sales/metrics/nope")

    assert resp.status_code == 404


async def test_a_reader_cannot_delete_a_definition(client, auth_client, ws, reader):
    await build_model(auth_client, ws)
    await client.post("/auth/login", json={"email": "reader@s.local", "password": "pw"})

    resp = await client.delete(f"/workspaces/{ws}/semantic/models/sales/metrics/revenue")

    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        (
            "datasets",
            {
                "name": "orders",
                "catalog": "warehouse",
                "schema_name": "analytics",
                "table_name": "orders",
            },
        ),
        ("dimensions", {"name": "country", "dataset": "customers"}),
        ("metrics", {"name": "revenue", "dataset": "orders", "agg": "sum", "expr": "x"}),
        (
            "relationships",
            {
                "name": "orders_to_customers",
                "left_dataset": "orders",
                "right_dataset": "customers",
                "join_columns": [{"left": "customer_id", "right": "id"}],
            },
        ),
    ],
)
async def test_a_duplicate_name_is_a_conflict_not_a_server_error(auth_client, ws, kind, body):
    """Reusing a name is an ordinary thing to do in a form, not a server fault.

    Each child table is unique on `(model_id, name)`, and nothing converts an
    IntegrityError into a response — so without a check these all came back 500.
    """
    await build_model(auth_client, ws)

    resp = await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/{kind}", json=body)

    assert resp.status_code == 409
    assert body["name"] in str(resp.json()["message"])
