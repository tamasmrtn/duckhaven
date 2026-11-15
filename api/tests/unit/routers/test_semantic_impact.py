"""The impact gate: a semantic change that breaks a published dashboard is refused.

Dashboards read published models and metrics only. Removing or deprecating a
model, removing a metric or a dimension, or taking a metric back to draft breaks
every published dashboard that reads it, so each of those is a 409
``breaking_dashboards`` naming them, unless the caller passes ``allow_breaking``.
An import is checked the same way, and its dry run lists what it would break.
"""

from __future__ import annotations

import pytest

from api.services.dashboards import impact

from . import test_dashboards as dashboards
from .test_dashboards import _create, _publish, spec
from .test_semantic_import import DOCUMENT, SECOND_MODEL, _import

# The signed-in owner, and a workspace whose native `sales` model is published.
owner = dashboards.owner
as_owner = dashboards.as_owner
ws = dashboards.ws

#: The import tests' model under another slug, so it does not meet the native one.
SHOP = DOCUMENT.replace("slug: sales", "slug: shop").replace("name: Sales", "name: Shop")
REVENUE = """\
      - name: revenue
        dataset: orders
        agg: sum
        expr: total_amount
        measured_on: order_date
        synonyms: [turnover]
"""
WITHOUT_REVENUE = SHOP.replace(
    REVENUE, "      - name: order_count\n        dataset: orders\n        agg: count\n"
)

SHOP_BOARD = spec(
    name="shop-overview",
    parameters={},
    rows=[
        {
            "widgets": [
                {
                    "id": "revenue",
                    "type": "kpi",
                    "title": "Revenue",
                    "query": {"model": "shop", "metrics": ["revenue"]},
                }
            ]
        }
    ],
)


async def _published(client, ws: str, body: dict | None = None) -> None:
    board = await _create(client, ws, body)
    await _publish(client, ws, board["name"], board["version"])


def _breaks(resp) -> dict[str, list[str]]:
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "breaking_dashboards"
    return {d["name"]: d["reads"] for d in body["details"]["affected_dashboards"]}


async def _model(client, ws: str, slug: str) -> dict:
    model = (await client.get(f"/workspaces/{ws}/semantic/models/{slug}")).json()
    return {
        "status": model["status"],
        "metrics": sorted(m["name"] for m in model["metrics"]),
        "dimensions": sorted(d["name"] for d in model["dimensions"]),
    }


# ── Changes made in DuckHaven ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body", "reads"),
    [
        ("DELETE", "/metrics/revenue", None, ["sales.revenue"]),
        ("DELETE", "/dimensions/country", None, ["sales.country"]),
        ("PATCH", "/metrics/revenue", {"status": "draft"}, ["sales.revenue"]),
        ("POST", "/deprecate", None, ["sales"]),
        ("DELETE", "", None, ["sales"]),
    ],
)
async def test_a_change_that_breaks_a_published_dashboard_needs_allow_breaking(
    as_owner, ws, method, path, body, reads
):
    await _published(as_owner, ws)
    before = await _model(as_owner, ws, "sales")
    url = f"/workspaces/{ws}/semantic/models/sales{path}"
    kwargs = {"json": body} if body is not None else {}

    refused = await as_owner.request(method, url, **kwargs)

    assert _breaks(refused) == {"sales-overview": reads}
    assert "breaks 1 published dashboard: sales-overview" in refused.json()["message"]
    assert await _model(as_owner, ws, "sales") == before
    allowed = await as_owner.request(method, url, params={"allow_breaking": "true"}, **kwargs)
    assert allowed.status_code in (200, 204), allowed.text


async def test_a_draft_dashboard_does_not_hold_a_change_back(as_owner, ws):
    """Publishing the draft checks it again, against the model as it is then."""
    await _create(as_owner, ws)

    resp = await as_owner.delete(f"/workspaces/{ws}/semantic/models/sales/metrics/revenue")

    assert resp.status_code == 204


async def test_a_change_no_published_dashboard_reads_goes_through(as_owner, ws):
    notes = [{"widgets": [{"id": "notes", "type": "text", "markdown": "Net of refunds."}]}]
    await _published(as_owner, ws, spec(parameters={}, rows=notes))
    url = f"/workspaces/{ws}/semantic/models/sales/metrics/revenue"

    assert (await as_owner.patch(url, json={"description": "Net."})).status_code == 200
    assert (await as_owner.delete(url)).status_code == 204


def test_a_refusal_names_ten_dashboards_and_counts_the_rest():
    found = [{"name": f"board-{i:02}", "title": "B", "reads": ["sales"]} for i in range(12)]

    detail = impact.refusal("Deleting 'sales'", found).detail

    assert detail["detail"].startswith("Deleting 'sales' breaks 12 published dashboards: board-00")
    assert "board-09 and 2 more." in detail["detail"]
    assert len(detail["affected_dashboards"]) == 12


# ── Imports ───────────────────────────────────────────────────────────────────


async def _shop(client, ws: str) -> None:
    """The `shop` model imported and published, and a published dashboard on its revenue."""
    imported = await _import(client, ws, SHOP)
    assert imported.status_code == 200, imported.text
    published = await client.post(f"/workspaces/{ws}/semantic/models/shop/publish")
    assert published.status_code == 200, published.text
    await _published(client, ws, SHOP_BOARD)


async def test_an_import_that_breaks_a_dashboard_is_refused_and_changes_nothing(as_owner, ws):
    await _shop(as_owner, ws)
    before = await _model(as_owner, ws, "shop")

    resp = await _import(as_owner, ws, WITHOUT_REVENUE)

    assert _breaks(resp) == {"shop-overview": ["shop.revenue"]}
    assert resp.json()["message"].startswith("This import breaks 1 published dashboard")
    assert await _model(as_owner, ws, "shop") == before


async def test_a_dry_run_lists_what_an_import_removes_and_breaks(as_owner, ws):
    await _shop(as_owner, ws)
    before = await _model(as_owner, ws, "shop")

    resp = await _import(as_owner, ws, WITHOUT_REVENUE, dry_run="true")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["dry_run"], body["updated"]) == (True, 1)
    assert body["removed_metrics"] == ["shop.revenue"]
    assert body["removed_models"] == body["removed_dimensions"] == []
    assert [(d["name"], d["title"], d["reads"]) for d in body["affected_dashboards"]] == [
        ("shop-overview", "Sales", ["shop.revenue"])
    ]
    assert await _model(as_owner, ws, "shop") == before


async def test_allow_breaking_imports_and_lists_what_it_broke(as_owner, ws):
    await _shop(as_owner, ws)

    resp = await _import(as_owner, ws, WITHOUT_REVENUE, allow_breaking="true")

    assert resp.status_code == 200, resp.text
    assert [d["name"] for d in resp.json()["affected_dashboards"]] == ["shop-overview"]
    assert (await _model(as_owner, ws, "shop"))["metrics"] == ["order_count"]


async def test_an_import_that_breaks_nothing_says_what_it_removed(as_owner, ws):
    """A draft model no dashboard can read loses a metric without a word of warning."""
    await _import(as_owner, ws, SHOP)

    body = (await _import(as_owner, ws, WITHOUT_REVENUE)).json()

    assert body["removed_metrics"] == ["shop.revenue"]
    assert (body["dry_run"], body["affected_dashboards"]) == (False, [])


async def test_retiring_a_model_a_dashboard_reads_is_refused(as_owner, ws):
    await _shop(as_owner, ws)

    retired = await _import(as_owner, ws, SECOND_MODEL)

    assert _breaks(retired) == {"shop-overview": ["shop"]}
    kept = await _import(as_owner, ws, SECOND_MODEL, reconcile="none")
    assert kept.status_code == 200, kept.text
    assert kept.json()["removed_models"] == []


async def test_purging_a_provider_a_dashboard_reads_needs_allow_breaking(as_owner, ws):
    await _shop(as_owner, ws)
    url = f"/workspaces/{ws}/semantic/imports"

    refused = await as_owner.delete(url, params={"provider": "duckhaven"})

    assert _breaks(refused) == {"shop-overview": ["shop"]}
    allowed = await as_owner.delete(url, params={"provider": "duckhaven", "allow_breaking": "true"})
    assert allowed.status_code == 204, allowed.text
