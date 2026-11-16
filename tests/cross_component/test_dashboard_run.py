"""A dashboard view across the real API and agent.

The dashboard reads an Iceberg table through a published semantic model and an
SQL widget. Its queries go to the live agent as batches, which send small results
back inline, and a second view is answered by the result cache: with its compute
stopped too. A catalog in scoped mode decides, viewer by viewer, which widgets
show data.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
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


async def _view(client: httpx.AsyncClient, dashboard: str) -> list[dict]:
    return _events((await client.post(dashboard, json={}, timeout=120)).text)


def _by_widget(events: list[dict]) -> dict[str, dict]:
    return {e["id"]: e for e in events if e["type"] == "widget"}


async def _until_cached(client: httpx.AsyncClient, dashboard: str) -> list[dict]:
    """Views until one is answered wholly from the cache, which admits a run just
    after it reports done."""
    async with asyncio.timeout(30):
        while True:
            events = await _view(client, dashboard)
            if events[-1].get("cache_hits") == 3:
                return events
            await asyncio.sleep(0.5)


async def _extra_agent(api_client: httpx.AsyncClient, spawn_agent) -> tuple[object, str]:
    """A disposable agent, once it reports the extensions a dashboard needs."""
    known = {a["id"] for a in (await api_client.get("/api/agents")).json()}
    proc = spawn_agent()
    async with asyncio.timeout(60):
        while True:
            for a in (await api_client.get("/api/agents")).json():
                extensions = (a.get("capabilities") or {}).get("extensions", [])
                if a["id"] not in known and a["status"] == "healthy" and "httpfs" in extensions:
                    return proc, a["id"]
            await asyncio.sleep(0.5)


async def test_a_view_the_cache_answers_needs_no_compute(
    api_client, workspace, dashboard, spawn_agent
):
    proc, agent_id = await _extra_agent(api_client, spawn_agent)
    # The view's compute is this agent alone: once it stops, nothing else runs it.
    await _ok(
        await api_client.patch(f"/api/workspaces/{workspace}", json={"default_agent_id": agent_id})
    )
    first = _by_widget(await _view(api_client, dashboard))
    assert {w["state"] for w in first.values()} == {"ok"}, first
    ran = await _ok(await api_client.get(f"/api/queries/{first['latest']['rows']['query_id']}"))
    assert ran["agent_id"] == agent_id
    await _until_cached(api_client, dashboard)

    proc.terminate()
    proc.wait(timeout=15)
    async with asyncio.timeout(30):
        while any(
            a["id"] == agent_id and a["status"] == "healthy"
            for a in await _ok(await api_client.get("/api/agents"))
        ):
            await asyncio.sleep(0.5)

    events = await _view(api_client, dashboard)
    assert [e for e in events if e["type"] == "compute"] == []
    widgets = _by_widget(events)
    assert {(w["state"], w["cache"]) for w in widgets.values()} == {("ok", "hit")}, widgets
    assert widgets["by_country"]["rows"]["data"] == first["by_country"]["rows"]["data"]
    assert events[-1]["cache_hits"] == 3
    assert events[-1]["batch"]["used"] is False


async def test_a_scoped_grant_decides_what_each_viewer_sees(
    stack, api_client, workspace, catalog, dashboard
):
    base = f"/api/workspaces/{workspace}"
    suffix = uuid.uuid4().hex[:8]
    viewers: dict[str, httpx.AsyncClient] = {}
    ids: dict[str, str] = {}
    for name in ("granted", "ungranted"):
        email, password = f"{name}-{suffix}@xc.test", "xc-password-123"
        user = await _ok(
            await api_client.post(
                "/api/admin/users", json={"email": email, "name": name, "password": password}
            ),
            201,
        )
        ids[name] = user["id"]
        await _ok(
            await api_client.post(
                f"{base}/members", json={"user_id": user["id"], "role": "reader"}
            ),
            201,
        )
        client = httpx.AsyncClient(base_url=stack.base_url, timeout=30.0)
        await _ok(await client.post("/api/auth/login", json={"email": email, "password": password}))
        viewers[name] = client
    await _ok(
        await api_client.patch(
            f"{base}/catalogs/{catalog}/access-mode", json={"access_mode": "scoped"}
        )
    )
    await _ok(
        await api_client.put(
            f"{base}/catalogs/{catalog}/grants",
            json={
                "user_id": ids["granted"],
                "schema_name": "analytics",
                "table_name": "orders",
                "tier": "reader",
            },
        ),
        201,
    )
    try:
        granted = _by_widget(await _view(viewers["granted"], dashboard))
        assert {w["state"] for w in granted.values()} == {"ok"}, granted
        assert float(granted["revenue"]["rows"]["data"][0][0]) == 175.0
        # Now cached: what the cache holds is not served to a viewer who may not read it.
        await _until_cached(viewers["granted"], dashboard)

        events = await _view(viewers["ungranted"], dashboard)
        denied = _by_widget(events)
        assert {w["state"] for w in denied.values()} == {"no_access"}, denied
        for widget in denied.values():
            assert "rows" not in widget and "sql" not in widget
            assert "orders" not in json.dumps(widget)
        assert events[-1]["cache_hits"] == 0
    finally:
        for client in viewers.values():
            await client.aclose()
