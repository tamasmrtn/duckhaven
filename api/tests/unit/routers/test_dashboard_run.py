"""Running a dashboard: each widget's query runs as the viewer and streams back.

Drives the real route, submit path, batch dispatch and QUERY_DONE handler. The
agent is a stand-in that runs each statement it is sent on a local DuckDB holding
the test tables, so the numbers a widget shows are the ones its SQL computes.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import tempfile
import uuid
from datetime import date, timedelta

import duckdb
import httpx
import pytest
import sqlalchemy as sa
from conftest import seed_owned_workspace
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.deps import get_session_factory
from api.main import api_app
from api.models.agent import Agent
from api.models.query import Query
from api.models.user import User
from api.models.workspace import Workspace
from api.services import grants as grant_service
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.dashboards import run as run_service
from api.services.semantic.timespec import today_in
from duckhaven_shared.protocol import Frame, FrameType

from . import test_dashboards
from .test_dashboards import _member, _sign_in
from .test_semantic import CUSTOMER_COLUMNS, ORDER_COLUMNS, build_model

# The signed-in owner fixtures, shared with the dashboard store tests.
owner = test_dashboards.owner
as_owner = test_dashboards.as_owner

TODAY = today_in("UTC")

#: (id, customer_id, total_amount, status, order_date)
ORDERS = [
    (1, 1, 100.0, "paid", TODAY - timedelta(days=1)),
    (2, 2, 50.0, "paid", TODAY - timedelta(days=2)),
    (3, 1, 25.0, "test", TODAY - timedelta(days=3)),
    (4, 3, 40.0, "paid", TODAY - timedelta(days=45)),
]
CUSTOMERS = [(1, "NL"), (2, "DE"), (3, "NL")]


def _kpi(id: str, metric: str = "revenue", **query) -> dict:
    return {
        "id": id,
        "type": "kpi",
        "title": id,
        "query": {"model": "sales", "metrics": [metric], "time_range": "$period", **query},
    }


BY_COUNTRY = {
    "id": "by_country",
    "type": "bar",
    "title": "By country",
    "query": {
        "model": "sales",
        "metrics": ["revenue"],
        "dimensions": ["country"],
        "time_range": "$period",
    },
}
ORDERS_TABLE = {
    "id": "orders",
    "type": "table",
    "title": "Orders",
    "sql": "SELECT id, total_amount FROM warehouse.analytics.orders "
    "WHERE order_date >= $since ORDER BY id",
    "params": {"since": "$period.start"},
}


def spec(*widgets: dict, **changes) -> dict:
    out = {
        "version": 1,
        "name": "sales-overview",
        "title": "Sales",
        "parameters": {
            "period": {"type": "time_range", "default": "last_30_days"},
            "country": {"type": "dimension", "model": "sales", "dimension": "country"},
        },
        "rows": [{"widgets": list(widgets or (_kpi("revenue"), BY_COUNTRY, ORDERS_TABLE))}],
    }
    out.update(changes)
    return copy.deepcopy(out)


# ── The stand-in agent ────────────────────────────────────────────────────────


class FakeAgent:
    """Runs what it is sent on DuckDB and answers as an agent does: a batch's
    results inline in QUERY_DONE, a single query's from its result server."""

    def __init__(self, agent_id: uuid.UUID, sessions, polaris) -> None:
        self.agent_id = agent_id
        self.sessions = sessions
        self.polaris = polaris
        self.frames: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.hold = False
        self.fail: str | None = None
        # The read of each batch, by position, that leaves its connection unusable,
        # as an agent's abandoned worker does: it fails, and the reads after it
        # fail with batch_aborted without running.
        self.abandon_at: int | None = None
        # Answer every query sent on its own with batch_aborted too.
        self.abort_alone = False
        self.running = 0
        self.most_running = 0
        self.tasks: set[asyncio.Task] = set()
        self.db = duckdb.connect()
        self.db.execute("ATTACH ':memory:' AS warehouse")
        self.db.execute("CREATE SCHEMA warehouse.analytics")
        self.db.execute(
            "CREATE TABLE warehouse.analytics.orders (id BIGINT, customer_id BIGINT, "
            "total_amount DOUBLE, status VARCHAR, order_date DATE)"
        )
        self.db.executemany("INSERT INTO warehouse.analytics.orders VALUES (?, ?, ?, ?, ?)", ORDERS)
        self.db.execute("CREATE TABLE warehouse.analytics.customers (id BIGINT, country VARCHAR)")
        self.db.executemany("INSERT INTO warehouse.analytics.customers VALUES (?, ?)", CUSTOMERS)

    def of(self, kind: FrameType) -> list[dict]:
        return [f for f in self.frames if f["type"] == kind]

    async def send_text(self, text: str) -> None:
        frame = json.loads(text)
        self.frames.append(frame)
        if frame["type"] == FrameType.DISPATCH_QUERY:
            work = [frame["payload"]]
        elif frame["type"] == FrameType.DISPATCH_BATCH:
            work = frame["payload"]["queries"]
        else:
            return
        task = asyncio.create_task(self._answer(work, frame["type"] == FrameType.DISPATCH_BATCH))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def release_once_running(self, count: int) -> None:
        """Let held answers go once ``count`` queries are in flight together.

        Answering at once lets one query finish before the next is sent, so how
        many ran together would depend on how fast this machine is. Holding them
        makes it what the runner sent. After 5s they go anyway, so a runner that
        never sends ``count`` fails its assertion rather than hanging.
        """
        try:
            async with asyncio.timeout(5):
                while self.running < count:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pass
        self.hold = False

    async def _answer(self, work: list[dict], inline: bool) -> None:
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        try:
            while self.hold:
                await asyncio.sleep(0.01)
            for n, item in enumerate(work):
                if inline and self.abandon_at is not None and n >= self.abandon_at:
                    error = "batch_aborted" if n > self.abandon_at else "could not be stopped"
                    await self._failed(item["query_id"], error)
                elif not inline and self.abort_alone:
                    await self._failed(item["query_id"], "batch_aborted")
                else:
                    await self._done(item["query_id"], item["sql"], inline)
        finally:
            self.running -= 1

    async def _failed(self, query_id: str, error: str) -> None:
        payload = {"query_id": query_id, "status": "failed", "error": error}
        async with self.sessions() as db:
            await query_service.handle_agent_frame(
                db,
                Frame(type=FrameType.QUERY_DONE, payload=payload),
                self.polaris,
                agent_id=self.agent_id,
            )

    async def _done(self, query_id: str, sql: str, inline: bool) -> None:
        payload: dict = {"query_id": query_id}
        if self.fail and self.fail in sql:
            payload.update(status="failed", error=f"Binder Error: no {self.fail}")
        else:
            rel = self.db.sql(sql)
            schema = [
                {"name": n, "type": str(t)} for n, t in zip(rel.columns, rel.types, strict=True)
            ]
            rows = len(rel.fetchall())
            fd, path = tempfile.mkstemp(suffix=".parquet")
            os.close(fd)
            try:
                self.db.sql(sql).write_parquet(path)
                with open(path, "rb") as f:
                    data = f.read()
            finally:
                os.unlink(path)
            self.files[query_id] = data
            payload.update(
                status="done",
                row_count=rows,
                duration_ms=3,
                result_bytes=len(data),
                result_path=f"/r/{query_id}.parquet",
                result_schema=schema,
            )
            if inline:
                payload["result_parquet"] = base64.b64encode(data).decode()
        async with self.sessions() as db:
            await query_service.handle_agent_frame(
                db,
                Frame(type=FrameType.QUERY_DONE, payload=payload),
                self.polaris,
                agent_id=self.agent_id,
            )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_engine(tmp_path):
    """A file database, unlike the other tests' shared in-memory one.

    A view runs its queries side by side, each in a session of its own. On the
    in-memory database every session shares one connection, and SQLite refuses
    one session's commit while another's statement is open. On a file each
    session has a connection, as on Postgres; WAL lets them read while one writes.
    """
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine

    from api.db.base import Base
    from api.services.rbac import seed_roles

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'run.db'}", connect_args={"timeout": 30}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _wal(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await seed_roles(session)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    api_app.dependency_overrides[get_session_factory] = lambda: factory
    return factory


@pytest.fixture
async def ws(as_owner, owner, db_session) -> str:
    """A workspace whose `sales` model is published, with `revenue` and `orders`."""

    slug = (await seed_owned_workspace(db_session, user_id=owner.id, slug="dash", name="Dash")).slug
    await as_owner.post(
        f"/workspaces/{slug}/catalogs",
        json={"name": "warehouse"},
    )
    await as_owner.post(
        f"/workspaces/{slug}/catalogs/warehouse/schemas", json={"name": "analytics"}
    )
    for name, columns in (("orders", ORDER_COLUMNS), ("customers", CUSTOMER_COLUMNS)):
        await as_owner.post(
            f"/workspaces/{slug}/catalogs/warehouse/schemas/analytics/tables",
            json={"name": name, "columns": columns},
        )
    await build_model(as_owner, slug)
    base = f"/workspaces/{slug}/semantic/models/sales"
    created = await as_owner.post(
        f"{base}/metrics",
        json={"name": "orders", "dataset": "orders", "agg": "count", "expr": "id"},
    )
    assert created.status_code == 201, created.text
    assert (await as_owner.post(f"{base}/publish")).status_code == 200
    for metric in ("revenue", "orders"):
        published = await as_owner.patch(f"{base}/metrics/{metric}", json={"status": "published"})
        assert published.status_code == 200, published.text
    return slug


async def _agent(
    db_session, sessions, polaris, features=("on_demand_attach", "exact_hugeint", "batch_dispatch")
):
    agent = Agent(
        name=f"dash-{uuid.uuid4().hex[:6]}",
        status="healthy",
        capabilities={
            "duckdb_version": "1.5.5",
            "extensions": ["httpfs", "iceberg"],
            "protocol_features": list(features),
            "timezone": "UTC",
        },
        result_host="agent.local",
        result_port=8001,
    )
    db_session.add(agent)
    await db_session.commit()
    fake = FakeAgent(agent.id, sessions, polaris)
    registry.register(agent.id, fake)  # type: ignore[arg-type]
    return fake


@pytest.fixture
async def agent(db_session, sessions, fake_polaris, monkeypatch):
    fake = await _agent(db_session, sessions, fake_polaris)

    async def proxy_rows(agent, query, *, row_offset=None, row_limit=None, token=None):
        return httpx.Response(200, content=fake.files[str(query.id)])

    monkeypatch.setattr(query_service, "proxy_rows", proxy_rows)
    yield fake
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


def _events(body: str) -> list[dict]:
    return [
        json.loads(chunk.removeprefix("data: "))
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]


def _widgets(events: list[dict]) -> dict[str, dict]:
    return {e["id"]: e for e in events if e["type"] == "widget"}


async def _view(client, ws: str, body: dict | None = None, name: str = "sales-overview"):
    resp = await client.post(f"/workspaces/{ws}/dashboards/{name}/run", json=body or {})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return _events(resp.text)


async def _publish(client, ws: str, body: dict) -> None:
    created = await client.post(f"/workspaces/{ws}/dashboards", json={"spec": body})
    assert created.status_code == 201, created.text
    published = await client.post(
        f"/workspaces/{ws}/dashboards/{body['name']}/publish",
        json={"base_version": created.json()["version"]},
    )
    assert published.status_code == 200, published.text


# ── A view ────────────────────────────────────────────────────────────────────


async def test_a_view_streams_every_widget_with_what_its_query_computed(as_owner, ws, agent):
    await _publish(as_owner, ws, spec())

    events = await _view(as_owner, ws)

    assert [e["type"] for e in events][0] == "start"
    assert events[-1]["type"] == "done"
    start = events[0]
    assert start["dashboard"]["name"] == "sales-overview"
    assert start["dashboard"]["draft"] is False
    assert start["dashboard"]["preview"] is None
    assert start["today"] == TODAY.isoformat()
    assert start["timezone"] == "UTC"
    [period, country] = start["parameters"]
    assert (period["name"], period["value"]) == ("period", "last_30_days")
    assert period["window"]["text"].startswith("Last 30 days: ")
    assert country["value"] is None
    assert start["widgets"] == ["revenue", "by_country", "orders"]

    widgets = _widgets(events)
    revenue = widgets["revenue"]
    assert revenue["state"] == "ok"
    # The test order is outside the metric, the 45-day-old one outside the window.
    [[total]] = revenue["rows"]["data"]
    assert float(total) == 150.0
    [column] = revenue["rows"]["columns"]
    assert (column["name"], column["role"], column["label"]) == ("revenue", "measure", "revenue")
    assert revenue["chart"]["type"] == "kpi"
    assert revenue["scope"]["applied"] == ["period"]
    assert revenue["scope"]["window"]["end"] == (TODAY + timedelta(days=1)).isoformat()
    assert revenue["definitions"][0]["name"] == "revenue"
    assert revenue["warnings"] == ["revenue: Excludes internal test orders."]
    assert revenue["cache"] in ("miss", "bypass", "ineligible")
    assert "SUM" in revenue["sql"].upper()

    bars = widgets["by_country"]
    assert bars["chart"]["orientation"] == "horizontal"
    assert [c["role"] for c in bars["rows"]["columns"]] == ["dimension", "measure"]
    assert sorted((c, float(v)) for c, v in bars["rows"]["data"]) == [("DE", 50.0), ("NL", 100.0)]

    table = widgets["orders"]
    assert [c["name"] for c in table["rows"]["columns"]] == ["id", "total_amount"]
    assert [row[0] for row in table["rows"]["data"]] == ["1", "2", "3"]
    assert table["scope"]["applied"] == ["period"]

    done = events[-1]
    assert done["widgets"] == {"ok": 3}
    assert done["batch"]["used"] is True
    # Every run is a dashboard's, tagged with where it came from.
    async with agent.sessions() as db:
        runs = (await db.scalars(sa.select(Query))).all()
    assert {q.origin for q in runs} == {"dashboard"}
    assert {q.tags["dashboard"] for q in runs} == {"sales-overview"}
    assert {q.tags["dashboard.run"] for q in runs} == {str(start["run_id"])}


async def test_widgets_asking_one_question_share_a_query(as_owner, ws, agent):
    await _publish(as_owner, ws, spec(_kpi("revenue"), _kpi("orders", "orders")))

    events = await _view(as_owner, ws)

    widgets = _widgets(events)
    assert [c["name"] for c in widgets["revenue"]["rows"]["columns"]] == ["revenue"]
    assert [c["name"] for c in widgets["orders"]["rows"]["columns"]] == ["orders"]
    assert widgets["orders"]["rows"]["data"] == [["3"]]
    assert widgets["revenue"]["rows"]["query_id"] == widgets["orders"]["rows"]["query_id"]
    assert events[-1]["statements"] == 1


async def test_a_kpi_compares_with_the_previous_period(as_owner, ws, agent):
    await _publish(
        as_owner,
        ws,
        spec(
            _kpi("revenue", compare="previous_period"),
            parameters={
                "period": {"type": "time_range", "default": "last_30_days"},
            },
        ),
    )

    events = await _view(as_owner, ws)

    revenue = _widgets(events)["revenue"]
    compare = revenue["compare"]
    assert float(compare["current"]) == 150.0
    assert float(compare["previous"]) == 40.0
    assert float(compare["delta"]) == 110.0
    assert compare["delta_pct"] == 2.75
    # Revenue has no polarity set, so a rise is neither good nor bad.
    assert compare["good"] is None
    assert compare["window_text"].startswith("Previous period: ")
    assert revenue["scope"]["compare_window"]["end"] == revenue["scope"]["window"]["start"]
    assert events[-1]["statements"] == 2


async def test_a_view_can_ask_a_kpi_for_a_comparison_its_file_lacks(as_owner, ws, agent):
    await _publish(
        as_owner,
        ws,
        spec(
            _kpi("revenue"),
            parameters={"period": {"type": "time_range", "default": "last_30_days"}},
        ),
    )

    plain = _widgets(await _view(as_owner, ws))["revenue"]
    compared = _widgets(await _view(as_owner, ws, {"compare": "previous_period"}))["revenue"]

    assert "compare" not in plain
    assert float(compared["compare"]["previous"]) == 40.0
    assert compared["compare"]["window_text"].startswith("Previous period: ")


async def test_a_comparison_the_run_does_not_know_is_refused(as_owner, ws, agent):
    await _publish(as_owner, ws, spec(_kpi("revenue")))
    resp = await as_owner.post(
        f"/workspaces/{ws}/dashboards/sales-overview/run", json={"compare": "last_week"}
    )
    assert resp.status_code == 422


async def test_a_parameter_filters_each_widget_and_says_where_it_did_not(as_owner, ws, agent):
    await _publish(as_owner, ws, spec())

    events = await _view(as_owner, ws, {"params": {"country": "NL"}})

    widgets = _widgets(events)
    assert widgets["revenue"]["scope"]["applied"] == ["period", "country"]
    assert float(widgets["revenue"]["rows"]["data"][0][0]) == 100.0
    # A bar of countries keeps every bar; an SQL widget takes only what it binds.
    assert widgets["by_country"]["scope"]["skipped"] == [
        {"parameter": "country", "reason": "own_dimension"}
    ]
    assert len(widgets["by_country"]["rows"]["data"]) == 2
    assert widgets["orders"]["scope"]["skipped"] == [
        {"parameter": "country", "reason": "not_bound"}
    ]


async def test_only_the_widgets_asked_for_run(as_owner, ws, agent):
    await _publish(as_owner, ws, spec())

    events = await _view(as_owner, ws, {"widgets": ["by_country"]})

    assert events[0]["widgets"] == ["by_country"]
    assert list(_widgets(events)) == ["by_country"]


async def test_a_failing_widget_fails_alone(as_owner, ws, agent):
    agent.fail = "customers"
    await _publish(as_owner, ws, spec())

    events = await _view(as_owner, ws)

    widgets = _widgets(events)
    assert widgets["by_country"]["state"] == "error"
    assert "no customers" in widgets["by_country"]["error"]
    assert "rows" not in widgets["by_country"]
    assert widgets["revenue"]["state"] == widgets["orders"]["state"] == "ok"
    assert events[-1]["widgets"] == {"ok": 2, "error": 1}


async def test_a_widget_the_viewer_may_not_read_says_so_and_names_nothing(
    as_owner, ws, agent, monkeypatch, db_session, client
):
    """Grants are checked for whoever is viewing, widget by widget."""
    await _publish(as_owner, ws, spec())
    await _member(db_session, ws, "reader")
    reader = (
        await db_session.execute(sa.select(User).where(User.email == "reader@d.local"))
    ).scalar_one()
    real_check = grant_service.assert_query_access

    async def check(db, workspace_id, principal_id, sql, *args, **kwargs):
        if principal_id == reader.id and "customers" in sql:
            raise grant_service.GrantDenied("Not authorized on warehouse.analytics.customers")
        return await real_check(db, workspace_id, principal_id, sql, *args, **kwargs)

    monkeypatch.setattr(grant_service, "assert_query_access", check)

    owner_view = _widgets(await _view(as_owner, ws))
    await _sign_in(client, "reader")
    reader_view = _widgets(await _view(client, ws))

    assert owner_view["by_country"]["state"] == "ok"
    denied = reader_view["by_country"]
    assert denied["state"] == "no_access"
    assert "customers" not in json.dumps(denied)
    assert "sql" not in denied and "rows" not in denied
    assert reader_view["revenue"]["state"] == "ok"


async def test_a_draft_says_what_it_cannot_read_widget_by_widget(as_owner, ws, agent):
    """A draft may name what publishing would refuse; each such widget says why."""
    created = await as_owner.post(
        f"/workspaces/{ws}/semantic/models", json={"slug": "unready", "name": "Unready"}
    )
    assert created.status_code == 201, created.text
    attach = {"id": "attach", "type": "table", "title": "Attach", "sql": "ATTACH 'x.db' AS x"}
    body = spec(
        _kpi("revenue"),
        _kpi("hidden", time_range=None) | {"query": {"model": "nope", "metrics": ["m"]}},
        _kpi("unready", time_range=None) | {"query": {"model": "unready", "metrics": ["m"]}},
        _kpi("typo", "revenu"),
        attach,
    )
    resp = await as_owner.post(f"/workspaces/{ws}/dashboards", json={"spec": body})
    assert resp.status_code == 201, resp.text

    events = await _view(as_owner, ws, {"draft": True})

    widgets = _widgets(events)
    assert widgets["revenue"]["state"] == "ok"
    assert widgets["hidden"] == {
        "type": "widget",
        "id": "hidden",
        "state": "no_access",
        "error": run_service.NO_ACCESS,
        "scope": widgets["hidden"]["scope"],
        "timing": widgets["hidden"]["timing"],
    }
    assert widgets["unready"]["state"] == "error"
    assert "published models only" in widgets["unready"]["error"]
    assert widgets["typo"]["state"] == "error"
    assert "revenu" in widgets["typo"]["error"]
    assert widgets["attach"]["state"] == "error"
    assert "ATTACH" in widgets["attach"]["error"]
    assert events[-1]["statements"] == 2


async def test_a_query_that_does_not_finish_in_time_is_cancelled(
    as_owner, ws, agent, monkeypatch, sessions
):
    agent.hold = True
    monkeypatch.setattr(run_service, "_wait_s", lambda query: 0.2)
    await _publish(as_owner, ws, spec(_kpi("revenue")))

    events = await _view(as_owner, ws)

    [widget] = _widgets(events).values()
    assert (widget["state"], widget["error"]) == ("error", "The query did not finish in time.")
    async with sessions() as db:
        [query] = (await db.scalars(sa.select(Query))).all()
    assert query.status == "cancelled"
    assert [f["payload"]["query_id"] for f in agent.of(FrameType.CANCEL_QUERY)] == [str(query.id)]
    agent.hold = False


async def test_a_run_that_breaks_says_so_and_stops(ws, agent, sessions, fake_polaris, monkeypatch):
    async def broken(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_service._Runner, "_execute", broken)
    stream = run_service.stream(await _plan(sessions, ws, fake_polaris), sessions, fake_polaris)

    events = [json.loads(f.removeprefix("data: ")) async for f in stream if f.startswith("data: ")]

    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["message"] == "The dashboard could not be run."


# ── Sending misses ────────────────────────────────────────────────────────────


def _many() -> list[dict]:
    # Distinct windows, so no two widgets share a query.
    return [
        _kpi(f"k{n}", time_range={"kind": "trailing", "grain": "day", "n": n + 2}) for n in range(8)
    ]


async def test_a_views_misses_go_to_the_agent_as_one_batch(as_owner, ws, agent, monkeypatch):
    # Lanes to spare: a batch opens a connection, which costs the agent more
    # than these reads, so the view still sends only one.
    monkeypatch.setattr(settings, "dashboard_max_inflight", 4)
    await _publish(as_owner, ws, spec(*_many()))

    events = await _view(as_owner, ws)

    [batch] = agent.of(FrameType.DISPATCH_BATCH)
    assert agent.of(FrameType.DISPATCH_QUERY) == []
    # In the page's order, so the top of the page comes first.
    widgets = _widgets(events)
    assert [q["sql"] for q in batch["payload"]["queries"]] == [
        widgets[f"k{n}"]["sql"] for n in range(8)
    ]
    assert events[-1]["batch"] == {"used": True, "batches": 1}
    assert events[-1]["widgets"] == {"ok": 8}


async def test_reads_a_broken_batch_skipped_are_sent_again_on_their_own(
    as_owner, ws, agent, db_session
):
    agent.abandon_at = 2
    await _publish(as_owner, ws, spec(*_many()))

    events = await _view(as_owner, ws)

    [batch] = agent.of(FrameType.DISPATCH_BATCH)
    sent = [q["query_id"] for q in batch["payload"]["queries"]]
    # The read that broke the connection fails; the five it stopped run alone.
    again = agent.of(FrameType.DISPATCH_QUERY)
    assert [f["payload"]["sql"] for f in again] == [
        q["sql"] for q in batch["payload"]["queries"][3:]
    ]
    widgets = _widgets(events)
    assert widgets["k2"]["state"] == "error"
    assert "could not be stopped" in widgets["k2"]["error"]
    assert [widgets[f"k{n}"]["state"] for n in (0, 1, 3, 4, 5, 6, 7)] == ["ok"] * 7
    assert events[-1]["widgets"] == {"ok": 7, "error": 1}
    # Each is a new query: History keeps the aborted runs as they ended.
    for n, query_id in zip(range(3, 8), sent[3:], strict=True):
        assert widgets[f"k{n}"]["rows"]["query_id"] != query_id
    aborted = (
        await db_session.execute(
            sa.select(Query).where(Query.id.in_([uuid.UUID(i) for i in sent[3:]]))
        )
    ).scalars()
    assert {(q.status, q.error) for q in aborted} == {("failed", "batch_aborted")}


async def test_a_read_is_sent_again_only_once(as_owner, ws, agent):
    agent.abandon_at = 0
    agent.abort_alone = True
    await _publish(as_owner, ws, spec(*_many()[:3]))

    events = await _view(as_owner, ws)

    assert len(agent.of(FrameType.DISPATCH_BATCH)) == 1
    assert len(agent.of(FrameType.DISPATCH_QUERY)) == 2
    widgets = _widgets(events)
    assert [widgets[f"k{n}"]["error"] for n in (1, 2)] == ["batch_aborted"] * 2
    assert events[-1]["widgets"] == {"error": 3}


async def test_batches_past_the_limit_run_side_by_side(as_owner, ws, agent, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_max_inflight", 2)
    monkeypatch.setattr(settings, "dashboard_batch_max_queries", 4)
    await _publish(as_owner, ws, spec(*_many()))
    agent.hold = True
    releasing = asyncio.create_task(agent.release_once_running(2))
    try:
        events = await _view(as_owner, ws)
    finally:
        await releasing

    batches = agent.of(FrameType.DISPATCH_BATCH)
    assert [len(f["payload"]["queries"]) for f in batches] == [4, 4]
    assert agent.most_running == 2
    # Dealt in turn: the first two widgets lead the two batches.
    widgets = _widgets(events)
    assert {f["payload"]["queries"][0]["sql"] for f in batches} == {
        widgets["k0"]["sql"],
        widgets["k1"]["sql"],
    }
    assert events[-1]["batch"] == {"used": True, "batches": 2}
    assert events[-1]["widgets"] == {"ok": 8}


async def test_a_batch_holds_no_more_than_the_limit(as_owner, ws, agent, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_max_inflight", 1)
    monkeypatch.setattr(settings, "dashboard_batch_max_queries", 3)
    await _publish(as_owner, ws, spec(*_many()))

    await _view(as_owner, ws)

    batches = agent.of(FrameType.DISPATCH_BATCH)
    assert sorted(len(f["payload"]["queries"]) for f in batches) == [2, 3, 3]
    # One lane: each batch waits for the one before it.
    assert agent.most_running == 1


async def test_an_agent_without_batches_gets_a_few_queries_at_a_time(
    as_owner, ws, db_session, sessions, fake_polaris, monkeypatch
):
    monkeypatch.setattr(settings, "dashboard_max_inflight", 2)
    fake = await _agent(
        db_session, sessions, fake_polaris, features=("on_demand_attach", "exact_hugeint")
    )

    async def proxy_rows(agent, query, *, row_offset=None, row_limit=None, token=None):
        return httpx.Response(200, content=fake.files[str(query.id)])

    monkeypatch.setattr(query_service, "proxy_rows", proxy_rows)
    await _publish(as_owner, ws, spec(*_many()))
    fake.hold = True
    releasing = asyncio.create_task(fake.release_once_running(2))
    try:
        events = await _view(as_owner, ws)
    finally:
        await releasing
        registry.unregister(fake.agent_id)

    assert len(fake.of(FrameType.DISPATCH_QUERY)) == 8
    assert fake.of(FrameType.DISPATCH_BATCH) == []
    assert fake.most_running == 2
    assert events[-1]["batch"] == {"used": False, "batches": 0}
    assert events[-1]["widgets"] == {"ok": 8}


# ── The result cache ──────────────────────────────────────────────────────────


async def test_a_view_from_the_cache_runs_nothing_and_starts_no_compute(
    as_owner, ws, agent, sessions, fake_polaris, monkeypatch
):
    from api.models.catalog import Catalog
    from api.services.compute import service as compute_service
    from api.services.polaris import PolarisTable, PolarisTableVersion
    from api.services.result_cache import service as result_cache

    monkeypatch.setattr(settings, "result_cache_enabled", True)
    monkeypatch.setattr(result_cache, "admission_session_factory", sessions)
    async with sessions() as db:
        catalog = (await db.scalars(sa.select(Catalog).where(Catalog.slug == "warehouse"))).one()
    for table in ("orders", "customers"):
        fake_polaris.tables[(catalog.polaris_name, "analytics", table)] = PolarisTable(
            name=table, catalog_name=catalog.polaris_name, schema_name="analytics"
        )
        fake_polaris.versions[(catalog.polaris_name, "analytics", table)] = PolarisTableVersion(
            table_uuid=f"u-{table}",
            snapshot_id=1,
            schema_id=0,
            metadata_location=f"s3://w/{table}/metadata/00001-0.json",
        )
    await _publish(as_owner, ws, spec())
    first = await _view(as_owner, ws)
    await result_cache.drain_admissions()
    assert {w["cache"] for w in _widgets(first).values()} == {"miss"}

    # Compute stops; nothing may start it again for a view the cache can answer.
    registry.unregister(agent.agent_id)
    sent = len(agent.frames)

    async def no_compute(*args, **kwargs):
        raise AssertionError("a cached view started compute")

    monkeypatch.setattr(compute_service, "ensure_agent", no_compute)
    second = await _view(as_owner, ws)

    widgets = _widgets(second)
    assert {w["cache"] for w in widgets.values()} == {"hit"}
    assert {w["state"] for w in widgets.values()} == {"ok"}
    assert widgets["by_country"]["rows"]["data"] == _widgets(first)["by_country"]["rows"]["data"]
    assert len(agent.frames) == sent
    assert second[-1]["cache_hits"] == 3


# ── Refusals before the stream ────────────────────────────────────────────────


async def test_what_cannot_run_is_refused_before_anything_runs(
    as_owner, ws, agent, client, db_session
):
    await _publish(as_owner, ws, spec())
    url = f"/workspaces/{ws}/dashboards/sales-overview/run"

    bad_value = await as_owner.post(url, json={"params": {"period": "fortnight"}})
    no_such = await as_owner.post(url, json={"params": {"region": "x"}})
    widget = await as_owner.post(url, json={"widgets": ["nope"]})
    missing = await as_owner.post(f"/workspaces/{ws}/dashboards/nope/run", json={})

    assert bad_value.status_code == 422
    assert bad_value.json()["error"] == "invalid_parameters"
    assert bad_value.json()["details"] == {"parameter": "period"}
    assert no_such.status_code == 422 and "region" in no_such.json()["message"]
    assert widget.status_code == 422 and widget.json()["error"] == "unknown_widget"
    assert missing.status_code == 404
    await _member(db_session, ws, "reader")
    await _sign_in(client, "reader")
    assert (await client.post(url, json={"draft": True})).status_code == 403
    assert agent.frames == []


async def test_a_writer_can_run_the_draft(as_owner, ws, agent):
    await _publish(as_owner, ws, spec())
    patched = await as_owner.patch(
        f"/workspaces/{ws}/dashboards/sales-overview",
        json={"base_version": 2, "spec": spec(_kpi("orders", "orders"))},
    )
    assert patched.status_code == 200, patched.text

    published = await _view(as_owner, ws)
    draft = await _view(as_owner, ws, {"draft": True})

    assert set(_widgets(published)) == {"revenue", "by_country", "orders"}
    assert list(_widgets(draft)) == ["orders"]
    assert draft[0]["dashboard"]["draft"] is True


# ── The stream itself ─────────────────────────────────────────────────────────


async def _plan(sessions, ws: str, fake_polaris, body: dict | None = None):
    from api.services.dashboards import loader
    from api.services.workspace import resolve_workspace_catalogs

    async with sessions() as db:
        workspace = (await db.scalars(sa.select(Workspace).where(Workspace.slug == ws))).one()
        user = (await db.scalars(sa.select(User).where(User.email == "owner@d.local"))).one()
        return await run_service.plan(
            db,
            workspace=workspace,
            catalogs=await resolve_workspace_catalogs(db, workspace.id),
            user=user,
            spec=loader.parse(body or spec()).spec,
            dashboard={"name": "sales-overview"},
            today=TODAY,
            values={},
        )


async def test_closing_the_stream_cancels_the_queries_still_running(
    ws, agent, sessions, fake_polaris
):
    agent.hold = True
    run = await _plan(sessions, ws, fake_polaris)
    stream = run_service.stream(run, sessions, fake_polaris)
    start = json.loads((await anext(stream)).removeprefix("data: "))
    assert start["type"] == "start"

    def sent() -> set[str]:
        frames = agent.of(FrameType.DISPATCH_BATCH)
        return {q["query_id"] for f in frames for q in f["payload"]["queries"]}

    # Closed once every query is running on the agent. Closed while the view's
    # lanes are still dispatching, the test database's single write lock is
    # held by the lanes being cancelled until their statements play out, and
    # the cancel can wait past its timeout; Postgres has no such lock.
    async with asyncio.timeout(5):
        while len(sent()) < len(run.statements):
            await asyncio.sleep(0.01)

    await stream.aclose()

    async with sessions() as db:
        statuses = {q.status for q in (await db.scalars(sa.select(Query))).all()}
    assert statuses == {"cancelled"}
    # Every query the agent holds is cancelled there.
    assert {f["payload"]["query_id"] for f in agent.of(FrameType.CANCEL_QUERY)} == sent()
    agent.hold = False


async def test_a_quiet_stream_says_it_is_still_there(
    ws, agent, sessions, fake_polaris, monkeypatch
):
    monkeypatch.setattr(run_service, "PING_S", 0.01)
    agent.hold = True
    stream = run_service.stream(await _plan(sessions, ws, fake_polaris), sessions, fake_polaris)
    try:
        frames = [await anext(stream) for _ in range(3)]
    finally:
        await stream.aclose()
        agent.hold = False

    assert ": ping\n\n" in frames


async def test_a_view_with_no_compute_up_says_compute_is_starting(
    ws, sessions, fake_polaris, monkeypatch
):
    from api.services.compute.backends import get_backend

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    monkeypatch.setattr(settings, "elastic_max_agents_per_pool", 1)
    get_backend("null")._instances.clear()
    stream = run_service.stream(await _plan(sessions, ws, fake_polaris), sessions, fake_polaris)
    try:
        events = []
        async with asyncio.timeout(5):
            while not events or events[-1]["type"] != "compute":
                frame = await anext(stream)
                if frame.startswith("data: "):
                    events.append(json.loads(frame.removeprefix("data: ")))
    finally:
        await stream.aclose()
        get_backend("null")._instances.clear()

    assert events[-1] == {
        "type": "compute",
        "state": "starting",
        "widgets": ["by_country", "orders", "revenue"],
    }
    async with sessions() as db:
        parked = (await db.scalars(sa.select(Query))).all()
    assert {(q.agent_id, q.origin, q.status) for q in parked} == {(None, "dashboard", "cancelled")}


async def test_a_view_decides_where_to_run_once_not_for_each_query(
    as_owner, ws, agent, sessions, monkeypatch
):
    decided = []
    resolve_compute = query_service.resolve_compute

    async def counted(*args, **kwargs):
        decided.append(kwargs.get("principal_id"))
        return await resolve_compute(*args, **kwargs)

    monkeypatch.setattr(query_service, "resolve_compute", counted)
    await _publish(as_owner, ws, spec())
    events = await _view(as_owner, ws)

    assert {w["state"] for w in _widgets(events).values()} == {"ok"}
    async with sessions() as db:
        ran = (await db.scalars(sa.select(Query))).all()
    assert len(ran) > 1
    assert len(decided) == 1


async def test_a_view_starts_a_stopped_workspace_default_once(
    ws, sessions, fake_polaris, monkeypatch
):
    from api.services.compute.backends import get_backend

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    get_backend("null")._instances.clear()
    async with sessions() as db:
        stopped = Agent(
            name="idle-default", status="unavailable", provider="null", lifecycle="terminated"
        )
        db.add(stopped)
        await db.flush()
        workspace = (await db.scalars(sa.select(Workspace).where(Workspace.slug == ws))).one()
        workspace.default_agent_id = stopped.id
        await db.commit()
    stream = run_service.stream(await _plan(sessions, ws, fake_polaris), sessions, fake_polaris)
    try:
        events = []
        async with asyncio.timeout(5):
            while not events or events[-1]["type"] != "compute":
                frame = await anext(stream)
                if frame.startswith("data: "):
                    events.append(json.loads(frame.removeprefix("data: ")))
    finally:
        await stream.aclose()
        get_backend("null")._instances.clear()

    async with sessions() as db:
        started = await db.get(Agent, stopped.id)
        parked = (await db.scalars(sa.select(Query))).all()
    # Every query of the view went to the agent decided for the view as a whole,
    # and the one that started it saved that.
    assert started.lifecycle == "provisioning"
    assert len(parked) > 1
    assert {(q.requested_agent_id, q.agent_id) for q in parked} == {(stopped.id, None)}


async def test_a_line_over_a_window_that_cuts_its_weeks_says_so(as_owner, ws, agent):
    """Four days hold no whole week, so every week the line draws is partial."""
    window = {
        "kind": "absolute",
        "start": (TODAY - timedelta(days=3)).isoformat(),
        "end": (TODAY + timedelta(days=1)).isoformat(),
    }
    weekly = {
        "id": "trend",
        "type": "line",
        "title": "Revenue by week",
        "query": {"model": "sales", "metrics": ["revenue"], "grain": "week", "time_range": window},
    }
    await _publish(as_owner, ws, spec(weekly))

    trend = _widgets(await _view(as_owner, ws))["trend"]

    weeks = [row[0] for row in trend["rows"]["data"]]
    assert weeks and trend["chart"]["x"]["partial"] == weeks
    assert [w for w in trend["warnings"] if "is partial" in w] == [
        f"The week {_week(w)} is partial: the window holds {_held(w, window)} of its 7 days."
        for w in weeks
    ]


def _week(value: str) -> str:
    from api.services.dashboards.resolve import Window

    start = date.fromisoformat(value[:10])
    return Window(start=start, end=start + timedelta(days=7), label="").text


def _held(value: str, window: dict) -> int:
    start = date.fromisoformat(value[:10])
    first = max(start, date.fromisoformat(window["start"]))
    return (min(start + timedelta(days=7), date.fromisoformat(window["end"])) - first).days


# ── Shaping ───────────────────────────────────────────────────────────────────


def _base(key: str) -> int:
    return run_service._colors("country", [key])[key]


def test_a_series_keeps_its_colour_when_others_are_filtered_away():
    """Colour follows the value, not its rank among the values shown."""
    keys: list[str] = []
    for n in range(100):
        if _base(f"c{n}") not in {_base(k) for k in keys}:
            keys.append(f"c{n}")
        if len(keys) == 5:
            break

    shown = run_service._colors("country", keys)
    filtered = run_service._colors("country", keys[2:])

    assert shown == {k: _base(k) for k in keys}
    assert filtered == {k: shown[k] for k in keys[2:]}


def test_two_values_never_share_a_colour():
    keys = [f"v{n}" for n in range(8)]

    assert sorted(run_service._colors("country", keys).values()) == list(range(8))


def test_colours_stop_once_every_slot_is_taken():
    """Past the eighth key there is no slot left. The rest get none, and draw in a
    neutral colour, rather than the search for a free slot running forever."""
    keys = [f"v{n}" for n in range(12)]

    slots = run_service._colors("country", keys)

    assert len(slots) == 8
    assert sorted(slots.values()) == list(range(8))


def test_past_eight_series_the_largest_are_drawn():
    from api.services.dashboards.chart_spec import ChartSpec, Limits, Measure, Series

    chart = ChartSpec(
        type="line",
        y=[Measure(field="revenue", label="Revenue")],
        series=Series(field="country"),
        limits=Limits(rows=None),
    )
    data = [["2026-01-01", f"c{n}", str(n)] for n in range(10)]

    keys, dropped = run_service._series(chart, ["month", "country", "revenue"], data)

    assert keys == [f"c{n}" for n in range(2, 10)]
    assert dropped == 2


def _weekly(window_start: date, window_end: date, weeks: list[str]) -> tuple[list[str], list[str]]:
    """The partial weeks of a weekly line over a window, and what the view says."""
    from api.services.dashboards.chart_spec import Axis, ChartSpec, Limits, Measure
    from api.services.dashboards.resolve import Window

    chart = ChartSpec(
        type="line",
        x=Axis(field="week", kind="time", grain="week"),
        y=[Measure(field="revenue", label="Revenue")],
        limits=Limits(rows=None),
    )
    data = [[week, "1"] for week in weeks] + [[None, "1"]]
    notes = run_service._partial(
        chart, ["week", "revenue"], data, Window(start=window_start, end=window_end, label="")
    )
    return chart.x.partial, notes


def test_a_week_the_window_cuts_is_marked_partial():
    """A weekly line over the last 90 days starts mid-week. Its first week holds
    a few days' revenue and reads as a dip, though nothing fell."""
    # Wednesday 19 August to Thursday 12 November, the day after it excluded.
    partial, notes = _weekly(
        date(2026, 8, 19),
        date(2026, 11, 13),
        ["2026-08-17T00:00:00", "2026-08-24T00:00:00", "2026-11-02T00:00:00", "2026-11-09"],
    )

    assert partial == ["2026-08-17T00:00:00", "2026-11-09"]
    assert notes == [
        "The week 17 – 23 Aug 2026 is partial: the window holds 5 of its 7 days.",
        "The week 9 – 15 Nov 2026 is partial: the window holds 4 of its 7 days.",
    ]


def test_whole_periods_are_not_marked():
    assert _weekly(date(2026, 8, 17), date(2026, 8, 31), ["2026-08-17", "2026-08-24"]) == ([], [])


def test_series_come_in_name_order_whatever_order_the_rows_came_in():
    """A legend lists the series in this order. In the rows' order, a filter that
    changes which value comes first would reorder the legend, though no colour
    moved. A missing value comes last."""
    from api.services.dashboards.chart_spec import ChartSpec, Limits, Measure, Series

    chart = ChartSpec(
        type="line",
        y=[Measure(field="revenue", label="Revenue")],
        series=Series(field="channel"),
        limits=Limits(rows=None),
    )
    names = ["week", "channel", "revenue"]
    rows = [["2026-10-05", "Web", "3"], ["2026-10-05", None, "1"], ["2026-10-05", "Partner", "2"]]
    filtered = [["2026-10-05", "Retail", "4"], *rows]

    assert run_service._series(chart, names, rows) == (["Partner", "Web", ""], 0)
    assert run_service._series(chart, names, filtered)[0] == ["Partner", "Retail", "Web", ""]


def _drawn(widget: dict, columns: list[str], rows: list[list]) -> list[list]:
    """The rows a view sends for ``widget``, given a result in this order."""
    from api.schemas.query import RowsPageOut
    from api.services.dashboards.chart_spec import chart_spec
    from api.services.dashboards.spec import DashboardSpec

    spec = DashboardSpec.model_validate(
        {"version": 1, "name": "d", "title": "D", "rows": [{"widgets": [widget]}]}
    )
    parsed = spec.rows[0].widgets[0]
    page = RowsPageOut(
        rows=[dict(zip(columns, row, strict=True)) for row in rows],
        columns=columns,
        cursor=None,
        total=len(rows),
        column_schema=None,
    )
    return run_service.drawn(parsed, chart_spec(parsed), page)[1]


def test_a_lines_rows_come_oldest_first_whatever_order_the_query_returned():
    """A metric query has no order of its own, and the table view, a CSV and a
    model reading the first few rows all read them as they come."""
    weekly = {
        "id": "trend",
        "type": "line",
        "title": "Revenue by week",
        "query": {"model": "shop", "metrics": ["revenue"], "grain": "week"},
    }
    rows = [
        ["2026-10-26T00:00:00", "43178.48"],
        [None, "1"],
        ["2026-08-31T00:00:00", "40376.82"],
        ["2026-09-21T00:00:00", "43098.11"],
    ]

    assert _drawn(weekly, ["week", "revenue"], rows) == [
        ["2026-08-31T00:00:00", "40376.82"],
        ["2026-09-21T00:00:00", "43098.11"],
        ["2026-10-26T00:00:00", "43178.48"],
        [None, "1"],
    ]


def test_a_line_split_by_a_dimension_keeps_each_periods_rows_together():
    split = {
        "id": "by_channel",
        "type": "line",
        "title": "Revenue by month, per channel",
        "query": {
            "model": "shop",
            "metrics": ["revenue"],
            "dimensions": ["channel"],
            "grain": "month",
        },
    }
    rows = [
        ["Web", "2026-10-01", "5"],
        ["Retail", "2026-09-01", "4"],
        ["Web", "2026-09-01", "3"],
        ["Retail", "2026-10-01", "6"],
    ]

    # Stable: within a month, the rows keep the query's order.
    assert _drawn(split, ["channel", "month", "revenue"], rows) == [
        ["Retail", "2026-09-01", "4"],
        ["Web", "2026-09-01", "3"],
        ["Web", "2026-10-01", "5"],
        ["Retail", "2026-10-01", "6"],
    ]


def test_an_sql_lines_numeric_axis_sorts_as_numbers():
    yearly = {
        "id": "yearly",
        "type": "line",
        "title": "Orders by year",
        "sql": "SELECT year, orders FROM t",
        "columns": {"x": "year", "y": ["orders"]},
    }

    drawn = _drawn(yearly, ["year", "orders"], [[2025, 9], [998, 1], [2024, 7]])

    assert [row[0] for row in drawn] == [998, 2024, 2025]


def test_bars_and_tables_keep_the_querys_order():
    """A bar's order is its ranking, and a table's is what its query asked for."""
    bar = {
        "id": "by_country",
        "type": "bar",
        "title": "Revenue by country",
        "query": {"model": "shop", "metrics": ["revenue"], "dimensions": ["country"]},
    }
    rows = [["NL", "9"], ["BE", "7"], ["DE", "8"]]

    assert _drawn(bar, ["country", "revenue"], rows) == rows
    table = {**bar, "id": "rows", "type": "table"}
    assert _drawn(table, ["country", "revenue"], rows) == rows


@pytest.mark.parametrize(
    ("current", "previous", "polarity", "delta", "pct", "good"),
    [
        ("150", "100", "up_good", "50", 0.5, True),
        ("150", "100", "down_good", "50", 0.5, False),
        (80, 100, "up_good", -20, -0.2, False),
        (1.5, 2, "neutral", -0.5, -0.25, None),
        ("5", "0", "up_good", "5", None, True),
        ("5", None, "up_good", None, None, None),
        ("7", "7", "up_good", "0", 0.0, None),
    ],
)
def test_a_comparison_says_how_much_it_moved_and_whether_that_is_good(
    current, previous, polarity, delta, pct, good
):
    from api.schemas.query import RowsPageOut
    from api.services.dashboards.chart_spec import ChartSpec, Limits, Measure
    from api.services.dashboards.resolve import ResolvedWidget, Window

    widget = run_service._Widget(
        widget=None,  # type: ignore[arg-type]
        resolved=ResolvedWidget(
            id="k", compare=Window(start=TODAY, end=TODAY + timedelta(days=1), label="Previous")
        ),
        chart=ChartSpec(
            type="kpi",
            y=[Measure(field="revenue", label="Revenue", polarity=polarity)],
            limits=Limits(rows=1),
        ),
    )
    before = RowsPageOut(
        rows=[] if previous is None else [{"revenue": previous}],
        columns=["revenue"],
        cursor=None,
        total=0,
    )

    out = run_service._compare(widget, [[current]], ["revenue"], before)

    assert (out["delta"], out["delta_pct"], out["good"]) == (delta, pct, good)
    assert out["window_text"].startswith("Previous: ")
