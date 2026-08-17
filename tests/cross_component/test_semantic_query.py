"""The semantic layer end to end: real API + real agent + real catalog.

The unit suite proves the compiler emits the SQL it means to. This proves the
SQL is *right* — that running it against real data returns the number a
hand-written control query returns.

The fan-out case is the one that earns this file. A `many_to_one` join is only
safe if the right-hand side really is unique on the join key, and no amount of
testing the generator can show that the generated join does not multiply rows.
Only executing it against real data can.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.cross_component


async def _run(api_client, workspace: str, agent_id: str, sql: str) -> dict:
    created = await api_client.post(
        f"/api/workspaces/{workspace}/queries", json={"sql": sql, "agent_id": agent_id}
    )
    assert created.status_code == 202, created.text
    query_id = created.json()["id"]

    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        body = (await api_client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"query {query_id} did not finish in time")


async def _rows(api_client, query_id: str) -> list[dict]:
    resp = await api_client.get(f"/api/queries/{query_id}/rows", params={"limit": 100})
    assert resp.status_code == 200, resp.text
    return resp.json()["rows"]


async def _scalar(api_client, workspace: str, agent_id: str, sql: str):
    done = await _run(api_client, workspace, agent_id, sql)
    assert done["status"] == "done", done
    rows = await _rows(api_client, done["id"])
    return next(iter(rows[0].values()))


@pytest.fixture
def catalog(workspace: str) -> str:
    return f"c_{workspace.replace('-', '_')}"


async def _seed(api_client, workspace: str, agent_id: str) -> None:
    """A two-table star with a deliberate fan-out trap.

    ``sem_users`` has one row per user, so joining to it must not change the
    number of order rows. ``sem_orders`` includes a test order that the metric's
    filter is supposed to exclude, and one order per user plus a second for user
    1 — so a total that ignores the filter, or a join that multiplies, both come
    out visibly wrong.
    """
    await _run(
        api_client,
        workspace,
        agent_id,
        """
        CREATE TABLE sem_users AS
        SELECT * FROM (VALUES (1, 'GB'), (2, 'US'), (3, 'US'))
          AS t(user_id, country)
        """,
    )
    await _run(
        api_client,
        workspace,
        agent_id,
        """
        CREATE TABLE sem_orders AS
        SELECT * FROM (VALUES
            (1, 1, 100.0, 'placed', DATE '2026-01-15'),
            (2, 1,  50.0, 'placed', DATE '2026-02-10'),
            (3, 2, 200.0, 'placed', DATE '2026-02-20'),
            (4, 3, 400.0, 'test',   DATE '2026-02-25')
        ) AS t(id, user_id, total_amount, status, order_date)
        """,
    )


async def _define(api_client, workspace: str, catalog: str) -> None:
    base = f"/api/workspaces/{workspace}/semantic/models"
    assert (await api_client.post(base, json={"slug": "sales", "name": "Sales"})).status_code == 201
    model = f"{base}/sales"

    for name, key in (("sem_orders", ["id"]), ("sem_users", ["user_id"])):
        made = await api_client.post(
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
        await api_client.post(
            f"{model}/relationships",
            json={
                "name": "orders_to_users",
                "left_dataset": "sem_orders",
                "right_dataset": "sem_users",
                "join_columns": [{"left": "user_id", "right": "user_id"}],
            },
        )
    ).status_code == 201
    assert (
        await api_client.post(
            f"{model}/dimensions",
            json={
                "name": "order_date",
                "dataset": "sem_orders",
                "kind": "time",
                "is_default_time": True,
            },
        )
    ).status_code == 201
    assert (
        await api_client.post(
            f"{model}/dimensions",
            json={"name": "country", "dataset": "sem_users"},
        )
    ).status_code == 201
    assert (
        await api_client.post(
            f"{model}/metrics",
            json={
                "name": "revenue",
                "dataset": "sem_orders",
                "agg": "sum",
                "expr": "total_amount",
                "filter": "status <> 'test'",
                "time_dimension": "order_date",
            },
        )
    ).status_code == 201
    assert (
        await api_client.patch(f"{model}/metrics/revenue", json={"status": "published"})
    ).status_code == 200

    published = await api_client.post(f"{model}/publish")
    assert published.status_code == 200, published.text


async def _compile(api_client, workspace: str, body: dict) -> dict:
    resp = await api_client.post(f"/api/workspaces/{workspace}/semantic/compile", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_a_compiled_metric_returns_the_control_answer(
    api_client, workspace, healthy_agent, catalog
) -> None:
    agent = healthy_agent["id"]
    await _seed(api_client, workspace, agent)
    await _define(api_client, workspace, catalog)

    compiled = await _compile(api_client, workspace, {"model": "sales", "metrics": ["revenue"]})
    generated = await _scalar(api_client, workspace, agent, compiled["sql"])

    control = await _scalar(
        api_client,
        workspace,
        agent,
        "SELECT SUM(total_amount) FROM sem_orders WHERE status <> 'test'",
    )

    assert float(generated) == float(control) == 350.0


async def test_the_metric_filter_is_applied_without_being_asked_for(
    api_client, workspace, healthy_agent, catalog
) -> None:
    """The 400.0 test order must be absent from every answer this metric gives."""
    agent = healthy_agent["id"]
    await _seed(api_client, workspace, agent)
    await _define(api_client, workspace, catalog)

    compiled = await _compile(api_client, workspace, {"model": "sales", "metrics": ["revenue"]})
    generated = await _scalar(api_client, workspace, agent, compiled["sql"])

    unfiltered = await _scalar(
        api_client, workspace, agent, "SELECT SUM(total_amount) FROM sem_orders"
    )

    assert float(unfiltered) == 750.0
    assert float(generated) == 350.0


async def test_a_join_does_not_inflate_the_total(
    api_client, workspace, healthy_agent, catalog
) -> None:
    """The fan-out guarantee, checked against real rows.

    Grouping by a dimension on the joined table must not change the total. If
    the join multiplied fact rows, the sum of the groups would exceed the
    ungrouped answer — and nothing would error.
    """
    agent = healthy_agent["id"]
    await _seed(api_client, workspace, agent)
    await _define(api_client, workspace, catalog)

    total_sql = (await _compile(api_client, workspace, {"model": "sales", "metrics": ["revenue"]}))[
        "sql"
    ]
    total = float(await _scalar(api_client, workspace, agent, total_sql))

    by_country_sql = (
        await _compile(
            api_client,
            workspace,
            {"model": "sales", "metrics": ["revenue"], "dimensions": ["country"]},
        )
    )["sql"]
    done = await _run(api_client, workspace, agent, by_country_sql)
    assert done["status"] == "done", done
    rows = await _rows(api_client, done["id"])

    assert sum(float(r["revenue"]) for r in rows) == total
    assert {r["country"] for r in rows} == {"GB", "US"}


async def test_a_time_window_bounds_the_bound_column(
    api_client, workspace, healthy_agent, catalog
) -> None:
    agent = healthy_agent["id"]
    await _seed(api_client, workspace, agent)
    await _define(api_client, workspace, catalog)

    compiled = await _compile(
        api_client,
        workspace,
        {
            "model": "sales",
            "metrics": ["revenue"],
            "time_range": {
                "kind": "absolute",
                "start": "2026-02-01",
                "end": "2026-03-01",
            },
        },
    )
    generated = await _scalar(api_client, workspace, agent, compiled["sql"])

    # February's two non-test orders: 50 + 200.
    assert float(generated) == 250.0


async def test_the_compiled_sql_passes_the_ordinary_guards(
    api_client, workspace, healthy_agent, catalog
) -> None:
    """It goes through POST /queries like anything else — no separate path."""
    agent = healthy_agent["id"]
    await _seed(api_client, workspace, agent)
    await _define(api_client, workspace, catalog)

    compiled = await _compile(
        api_client,
        workspace,
        {"model": "sales", "metrics": ["revenue"], "dimensions": ["country"]},
    )
    submitted = await api_client.post(
        f"/api/workspaces/{workspace}/queries",
        json={"sql": compiled["sql"], "agent_id": agent},
    )

    assert submitted.status_code == 202, submitted.text


async def test_dropping_a_bound_table_breaks_the_metric_rather_than_deleting_it(
    api_client, workspace, healthy_agent, catalog
) -> None:
    agent = healthy_agent["id"]
    await _seed(api_client, workspace, agent)
    await _define(api_client, workspace, catalog)

    dropped = await api_client.delete(
        f"/api/workspaces/{workspace}/catalogs/{catalog}/schemas/analytics/tables/sem_orders"
    )
    assert dropped.status_code == 204, dropped.text

    body = (await api_client.get(f"/api/workspaces/{workspace}/semantic/models/sales")).json()

    orders = next(d for d in body["datasets"] if d["name"] == "sem_orders")
    assert orders["validation_state"] == "broken"
    # The definition survives, so it can be repaired rather than rewritten.
    assert any(m["name"] == "revenue" for m in body["metrics"])
