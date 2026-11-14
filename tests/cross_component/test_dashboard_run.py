"""A dashboard view across the real API and agent.

The dashboard reads an Iceberg table through a published semantic model and an
SQL widget. Its queries go to the live agent as batches, which send small results
back inline, and a second view is answered by the result cache.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

pytestmark = pytest.mark.cross_component

ROWS = [
    (1, "NL", 100.0, "2026-01-05"),
    (2, "DE", 50.0, "2026-01-20"),
    (3, "NL", 25.0, "2026-02-03"),
]


def _events(body: str) -> list[dict]:
    return [
        json.loads(chunk.removeprefix("data: "))
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]


async def _ok(response, status: int = 200):
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest_asyncio.fixture
async def dashboard(api_client, workspace, catalog, healthy_agent) -> str:
    """A published `orders` model and a dashboard over it."""
    base = f"/api/workspaces/{workspace}"
    await _ok(
        await api_client.post(
            f"{base}/catalogs/{catalog}/schemas/analytics/tables",
            json={
                "name": "orders",
                "columns": [
                    {"name": "id", "type": "BIGINT", "nullable": True},
                    {"name": "country", "type": "VARCHAR", "nullable": True},
                    {"name": "amount", "type": "DOUBLE", "nullable": True},
                    {"name": "order_date", "type": "DATE", "nullable": True},
                ],
            },
        ),
        201,
    )
    values = ", ".join(f"({i}, '{c}', {a}, DATE '{d}')" for i, c, a, d in ROWS)
    insert = await _ok(
        await api_client.post(
            f"{base}/queries",
            json={
                "sql": f"INSERT INTO {catalog}.analytics.orders VALUES {values}",
                "agent_id": healthy_agent["id"],
                "wait_timeout_s": 60,
            },
        ),
        200,
    )
    assert insert["status"] == "done", insert

    model = f"{base}/semantic/models/orders"
    await _ok(
        await api_client.post(f"{base}/semantic/models", json={"slug": "orders", "name": "O"}), 201
    )
    await _ok(
        await api_client.post(
            f"{model}/datasets",
            json={
                "name": "orders",
                "catalog": catalog,
                "schema_name": "analytics",
                "table_name": "orders",
                "primary_key": ["id"],
            },
        ),
        201,
    )
    for dimension in (
        {"name": "order_date", "dataset": "orders", "kind": "time", "is_default_time": True},
        {"name": "country", "dataset": "orders"},
    ):
        await _ok(await api_client.post(f"{model}/dimensions", json=dimension), 201)
    for metric in (
        {"name": "revenue", "dataset": "orders", "agg": "sum", "expr": "amount"},
        {"name": "orders", "dataset": "orders", "agg": "count", "expr": "id"},
    ):
        await _ok(await api_client.post(f"{model}/metrics", json=metric), 201)
    await _ok(await api_client.post(f"{model}/publish"))
    for metric in ("revenue", "orders"):
        await _ok(await api_client.patch(f"{model}/metrics/{metric}", json={"status": "published"}))

    window = {"kind": "absolute", "start": "2026-01-01", "end": "2026-03-01"}

    def kpi(id: str, metric: str) -> dict:
        query = {"model": "orders", "metrics": [metric], "time_range": "$period"}
        return {"id": id, "type": "kpi", "title": id, "query": query}

    spec = {
        "version": 1,
        "name": "orders",
        "title": "Orders",
        "parameters": {"period": {"type": "time_range", "default": window}},
        "rows": [
            {"widgets": [kpi("revenue", "revenue"), kpi("count", "orders")]},
            {
                "widgets": [
                    {
                        "id": "by_country",
                        "type": "bar",
                        "title": "By country",
                        "query": {
                            "model": "orders",
                            "metrics": ["revenue"],
                            "dimensions": ["country"],
                            "time_range": "$period",
                        },
                    },
                    {
                        "id": "latest",
                        "type": "table",
                        "title": "Latest",
                        "sql": f"SELECT id, amount FROM {catalog}.analytics.orders "
                        "WHERE order_date >= $since ORDER BY id",
                        "params": {"since": "$period.start"},
                    },
                ]
            },
        ],
    }
    created = await _ok(await api_client.post(f"{base}/dashboards", json={"spec": spec}), 201)
    await _ok(
        await api_client.post(
            f"{base}/dashboards/orders/publish", json={"base_version": created["version"]}
        )
    )
    return f"{base}/dashboards/orders/run"


async def test_a_view_runs_as_batches_and_a_second_comes_from_the_cache(api_client, dashboard):
    first = _events((await api_client.post(dashboard, json={}, timeout=120)).text)

    assert first[0]["type"] == "start" and first[-1]["type"] == "done", first[-1]
    widgets = {e["id"]: e for e in first if e["type"] == "widget"}
    assert {w["state"] for w in widgets.values()} == {"ok"}, widgets
    assert float(widgets["revenue"]["rows"]["data"][0][0]) == 175.0
    assert int(widgets["count"]["rows"]["data"][0][0]) == 3
    # The two numbers asked one question, so they shared one query.
    assert widgets["revenue"]["rows"]["query_id"] == widgets["count"]["rows"]["query_id"]
    assert sorted((c, float(v)) for c, v in widgets["by_country"]["rows"]["data"]) == [
        ("DE", 50.0),
        ("NL", 125.0),
    ]
    assert [row[0] for row in widgets["latest"]["rows"]["data"]] == ["1", "2", "3"]
    done = first[-1]
    assert done["statements"] == 3
    assert done["batch"]["used"] is True

    # A finished run is admitted to the cache just after it reports done.
    async with asyncio.timeout(30):
        while True:
            second = _events((await api_client.post(dashboard, json={}, timeout=120)).text)
            if second[-1].get("cache_hits") == 3:
                break
            await asyncio.sleep(0.5)

    cached = {e["id"]: e for e in second if e["type"] == "widget"}
    assert {w["cache"] for w in cached.values()} == {"hit"}, cached
    assert cached["by_country"]["rows"]["data"] == widgets["by_country"]["rows"]["data"]
    assert second[-1]["cache_hits"] == 3
    assert second[-1]["batch"]["used"] is False
