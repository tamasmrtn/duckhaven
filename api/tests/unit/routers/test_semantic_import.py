"""Importing semantic definitions, and the ownership rule that keeps it simple.

A model belongs to exactly one provider. That single decision is what removes the
whole class of merge problems an "import plus UI editing" design would otherwise
have: an import can never collide with a hand-authored definition, only with an
earlier version of itself, and the API refuses to edit what an import owns.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from api.models.storage_backend import StorageBackend
from api.models.user import User
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

DOCUMENT = """
version: 1
models:
  - slug: sales
    name: Sales
    description: Orders and revenue.
    datasets:
      - name: orders
        catalog: warehouse
        schema: analytics
        table: orders
        primary_key: [id]
      - name: customers
        catalog: warehouse
        schema: analytics
        table: customers
        primary_key: [id]
    relationships:
      - name: orders_to_customers
        left: orders
        right: customers
        join: [{left: customer_id, right: id}]
    dimensions:
      - name: order_date
        dataset: orders
        kind: time
        default_time: true
      - name: country
        dataset: customers
    metrics:
      - name: revenue
        dataset: orders
        agg: sum
        expr: total_amount
        measured_on: order_date
        synonyms: [turnover]
"""

SECOND_MODEL = """
version: 1
models:
  - slug: marketing
    name: Marketing
    datasets:
      - name: orders
        catalog: warehouse
        schema: analytics
        table: orders
    metrics:
      - name: order_count
        dataset: orders
        agg: count
"""


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="owner@i.local", password_hash=hash_password("pw"), name="Owner", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@i.local", "password": "pw"})
    return client


@pytest.fixture
async def ws(auth_client, owner, db_session):
    resp = await auth_client.post("/workspaces", json={"slug": "imp", "name": "Imp"})
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


async def _import(client, ws, document=DOCUMENT, **params):
    return await client.post(
        f"/workspaces/{ws}/semantic/imports/duckhaven",
        content=document,
        headers={"Content-Type": "text/plain"},
        params=params,
    )


async def test_a_document_imports(auth_client, ws):
    resp = await _import(auth_client, ws)

    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    assert resp.json()["skipped"] == []


async def test_imported_definitions_read_back_intact(auth_client, ws):
    await _import(auth_client, ws)

    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()

    assert body["provider"] == "duckhaven"
    assert {d["name"] for d in body["datasets"]} == {"orders", "customers"}
    metric = body["metrics"][0]
    assert metric["name"] == "revenue"
    assert metric["time_dimension"] == "order_date"
    assert metric["synonyms"] == ["turnover"]


async def test_an_import_arrives_as_a_draft(auth_client, ws):
    """An import is a pipeline publishing, not a person deciding."""
    await _import(auth_client, ws)

    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()

    assert body["status"] == "draft"


async def test_an_imported_model_cannot_be_edited_here(auth_client, ws):
    """One owner per model — which is why there is never a merge to resolve."""
    await _import(auth_client, ws)

    resp = await auth_client.patch(
        f"/workspaces/{ws}/semantic/models/sales", json={"name": "Renamed"}
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "imported_model"


async def test_reimporting_replaces_rather_than_duplicates(auth_client, ws):
    await _import(auth_client, ws)
    changed = DOCUMENT.replace("expr: total_amount", "expr: total_amount * 1.0")

    resp = await _import(auth_client, ws, changed)

    assert resp.json()["updated"] == 1
    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    assert len(body["metrics"]) == 1
    assert body["metrics"][0]["expr"] == "total_amount * 1.0"


async def test_reimporting_keeps_the_models_identity(auth_client, ws):
    """Same row, same id, same URL — a replace must not look like a delete."""
    await _import(auth_client, ws)
    before = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()["id"]

    await _import(auth_client, ws)
    after = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()["id"]

    assert before == after


async def test_reconcile_retires_a_model_the_producer_dropped(auth_client, ws):
    await _import(auth_client, ws)
    await _import(auth_client, ws, SECOND_MODEL, reconcile="none")
    assert len((await auth_client.get(f"/workspaces/{ws}/semantic/models")).json()) == 2

    resp = await _import(auth_client, ws, SECOND_MODEL, reconcile="provider_run")

    assert resp.json()["removed"] == 1
    slugs = {m["slug"] for m in (await auth_client.get(f"/workspaces/{ws}/semantic/models")).json()}
    assert slugs == {"marketing"}


async def test_reconcile_none_leaves_unmentioned_models_alone(auth_client, ws):
    """A partial publish must not delete what it simply did not mention."""
    await _import(auth_client, ws)

    await _import(auth_client, ws, SECOND_MODEL, reconcile="none")

    slugs = {m["slug"] for m in (await auth_client.get(f"/workspaces/{ws}/semantic/models")).json()}
    assert slugs == {"sales", "marketing"}


async def test_an_import_never_touches_a_hand_authored_model(auth_client, ws):
    """The collision case, refused and reported rather than silently overwritten."""
    await auth_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "sales", "name": "Mine"}
    )

    resp = await _import(auth_client, ws)

    assert resp.json()["created"] == 0
    assert any(s["reason"] == "slug_owned_by_other_provider" for s in resp.json()["skipped"])
    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()
    assert body["name"] == "Mine"
    assert body["provider"] == "native"


async def test_reconcile_does_not_retire_another_providers_models(auth_client, ws):
    await auth_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "mine", "name": "Mine"}
    )

    await _import(auth_client, ws, reconcile="provider_run")

    slugs = {m["slug"] for m in (await auth_client.get(f"/workspaces/{ws}/semantic/models")).json()}
    assert slugs == {"mine", "sales"}


async def test_the_native_provider_name_is_reserved(auth_client, ws):
    """Otherwise a client could forge human provenance on generated definitions."""
    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/imports/native",
        content=DOCUMENT,
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 422
    assert "reserved" in resp.json()["detail"]


async def test_an_unknown_provider_is_a_422(auth_client, ws):
    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/imports/lookml",
        content=DOCUMENT,
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 422
    assert "No semantic adapter" in resp.json()["detail"]


async def test_an_unknown_reconcile_mode_is_rejected(auth_client, ws):
    resp = await _import(auth_client, ws, reconcile="sometimes")

    assert resp.status_code == 422


async def test_unparseable_yaml_is_a_422(auth_client, ws):
    resp = await _import(auth_client, ws, "models: [\n  unbalanced")

    assert resp.status_code == 422


async def test_an_imported_model_can_be_validated_and_published(auth_client, ws):
    """Import, then a person decides — the two halves of the lifecycle."""
    await _import(auth_client, ws)

    report = (await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/validate")).json()
    assert report["ok"] is True

    resp = await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/publish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


async def test_purging_a_provider_removes_only_its_models(auth_client, ws):
    await auth_client.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "mine", "name": "Mine"}
    )
    await _import(auth_client, ws, reconcile="none")

    resp = await auth_client.delete(
        f"/workspaces/{ws}/semantic/imports", params={"provider": "duckhaven"}
    )

    assert resp.status_code == 204
    slugs = {m["slug"] for m in (await auth_client.get(f"/workspaces/{ws}/semantic/models")).json()}
    assert slugs == {"mine"}


async def test_a_json_artifact_is_accepted_without_a_text_content_type(auth_client, ws):
    """One route, two formats: a YAML document and a JSON manifest.

    The artifact is read as raw bytes, so a publisher posting `application/json`
    is not rejected for it.
    """
    import json

    resp = await auth_client.post(
        f"/workspaces/{ws}/semantic/imports/duckhaven",
        content=json.dumps({"models": [{"slug": "viajson", "name": "Via JSON"}]}),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1


async def test_purging_native_is_refused(auth_client, ws):
    resp = await auth_client.delete(
        f"/workspaces/{ws}/semantic/imports", params={"provider": "native"}
    )

    assert resp.status_code == 422


async def test_an_imported_models_metrics_are_reachable_once_it_is_published(auth_client, ws):
    """The lifecycle has to actually complete.

    Imported metrics cannot be promoted individually — the API refuses to edit an
    imported model — so if they arrived as drafts they could never become usable
    and publishing the model would produce a model with nothing in it. The model
    is the gate; the metrics inside it ride on that decision.
    """
    await _import(auth_client, ws)

    # Withheld while the model is still a draft.
    before = await auth_client.post(
        f"/workspaces/{ws}/semantic/compile",
        json={"model": "sales", "metrics": ["revenue"]},
    )
    assert before.status_code == 404, before.text

    published = await auth_client.post(f"/workspaces/{ws}/semantic/models/sales/publish")
    assert published.status_code == 200, published.text

    after = await auth_client.post(
        f"/workspaces/{ws}/semantic/compile",
        json={"model": "sales", "metrics": ["revenue"]},
    )
    assert after.status_code == 200, after.text
    assert "SUM(orders.total_amount)" in after.json()["sql"]


async def test_publishing_an_imported_model_is_still_a_deliberate_act(auth_client, ws):
    """It arrives as a draft, so nothing it defines answers a question by default."""
    await _import(auth_client, ws)

    body = (await auth_client.get(f"/workspaces/{ws}/semantic/models/sales")).json()

    assert body["status"] == "draft"
    hits = (
        await auth_client.get(f"/workspaces/{ws}/semantic/search", params={"q": "turnover"})
    ).json()
    assert hits["hits"] == []
