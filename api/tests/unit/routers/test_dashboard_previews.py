"""Previews: a dashboard file a writer tries out before saving it anywhere.

A preview runs exactly as a dashboard does, so these tests check what is its
own: who may see it, replacing its file, and when it expires. The run itself
uses the stand-in agent of ``test_dashboard_run``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from api.models.dashboard import DashboardPreview
from api.models.query import Query

from . import test_dashboard_run as runs
from .test_dashboard_run import _kpi, _widgets, spec
from .test_dashboards import _member, _sign_in

# The run tests' file database, signed-in owner, workspace and agent.
db_engine = runs.db_engine
sessions = runs.sessions
owner = runs.owner
as_owner = runs.as_owner
ws = runs.ws
agent = runs.agent

YAML = """\
# Trying out a count of orders.
version: 1
name: order-count
title: Orders
parameters:
  period: {type: time_range, default: last_30_days}
rows:
  - widgets:
      - id: count
        type: kpi
        title: Orders
        query: {model: sales, metrics: [orders], time_range: $period}
"""


def _previews(ws: str) -> str:
    return f"/workspaces/{ws}/dashboard-previews"


async def _create(client, ws: str, body: dict | None = None) -> dict:
    resp = await client.post(_previews(ws), json=body or {"spec": spec(_kpi("revenue"))})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run(client, ws: str, preview: dict, body: dict | None = None) -> list[dict]:
    resp = await client.post(f"{_previews(ws)}/{preview['id']}/run", json=body or {})
    assert resp.status_code == 200, resp.text
    return runs._events(resp.text)


async def test_a_preview_runs_a_file_nobody_has_saved(as_owner, ws, agent):
    preview = await _create(as_owner, ws)

    events = await _run(as_owner, ws, preview)

    start = events[0]
    assert start["dashboard"] == {
        "name": "sales-overview",
        "version": None,
        "spec_hash": preview["spec_hash"],
        "draft": False,
        "preview": preview["id"],
    }
    [[total]] = _widgets(events)["revenue"]["rows"]["data"]
    assert float(total) == 150.0
    assert events[-1]["type"] == "done"
    # Nothing was saved as a dashboard; History says the run was a preview's.
    listed = await as_owner.get(f"/workspaces/{ws}/dashboards")
    assert listed.json()["items"] == []
    async with agent.sessions() as db:
        [query] = (await db.scalars(sa.select(Query))).all()
    assert query.origin == "dashboard"
    assert query.tags["dashboard.preview"] == preview["id"]


async def test_replacing_the_file_changes_what_the_preview_runs(as_owner, ws, agent):
    preview = await _create(as_owner, ws)

    resp = await as_owner.put(f"{_previews(ws)}/{preview['id']}", json={"source_text": YAML})

    assert resp.status_code == 200, resp.text
    replaced = resp.json()
    assert replaced["id"] == preview["id"]
    assert replaced["spec_hash"] != preview["spec_hash"]
    assert replaced["source_text"] == YAML
    assert replaced["spec"]["name"] == "order-count"
    read = await as_owner.get(f"{_previews(ws)}/{preview['id']}")
    assert read.json() == replaced
    events = await _run(as_owner, ws, preview)
    assert events[0]["dashboard"]["name"] == "order-count"
    [[count]] = _widgets(events)["count"]["rows"]["data"]
    assert int(count) == 3


async def test_an_invalid_file_is_refused_and_the_preview_keeps_its_own(as_owner, ws):
    preview = await _create(as_owner, ws)
    broken = spec(_kpi("revenue"), _kpi("revenue"))

    replaced = await as_owner.put(f"{_previews(ws)}/{preview['id']}", json={"spec": broken})
    created = await as_owner.post(_previews(ws), json={"source_text": "version: 1\nrows: ["})

    for resp in (replaced, created):
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "invalid_dashboard"
        assert resp.json()["details"]["diagnostics"]
    read = await as_owner.get(f"{_previews(ws)}/{preview['id']}")
    assert read.json()["spec_hash"] == preview["spec_hash"]


async def test_a_preview_run_takes_what_a_dashboard_run_takes_but_a_draft(as_owner, ws, agent):
    preview = await _create(as_owner, ws)
    url = f"{_previews(ws)}/{preview['id']}/run"

    draft = await as_owner.post(url, json={"draft": True})
    unknown = await as_owner.post(url, json={"widgets": ["nope"]})

    assert draft.status_code == 422
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "unknown_widget"


async def test_only_its_writer_can_see_a_preview(as_owner, ws, agent, client, db_session):
    preview = await _create(as_owner, ws)
    url = f"{_previews(ws)}/{preview['id']}"
    await _member(db_session, ws, "writer")
    await _member(db_session, ws, "reader")

    await _sign_in(client, "writer")
    other = [
        await client.get(url),
        await client.put(url, json={"spec": spec()}),
        await client.post(f"{url}/run", json={}),
    ]
    await _sign_in(client, "reader")
    reader = [await client.post(_previews(ws), json={"spec": spec()}), await client.get(url)]

    assert [r.status_code for r in other] == [404, 404, 404]
    assert [r.status_code for r in reader] == [403, 403]


def _naive(at: str) -> datetime:
    """A UTC time, without its zone: SQLite gives times back without one."""
    return datetime.fromisoformat(at).replace(tzinfo=None)


async def _expire(sessions, preview: dict) -> None:
    async with sessions() as db:
        row = await db.get(DashboardPreview, uuid.UUID(preview["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()


async def test_a_preview_expires_two_hours_after_it_was_last_used(as_owner, ws, agent, sessions):
    preview = await _create(as_owner, ws)
    expires = _naive(preview["expires_at"])
    assert expires - _naive(preview["updated_at"]) == timedelta(hours=2)

    await _run(as_owner, ws, preview)
    async with sessions() as db:
        row = await db.get(DashboardPreview, uuid.UUID(preview["id"]))
    # Running it is using it.
    assert row.expires_at.replace(tzinfo=None) > expires

    await _expire(sessions, preview)
    gone = await as_owner.get(f"{_previews(ws)}/{preview['id']}")
    assert gone.status_code == 404
    assert "expires" in gone.json()["message"]
    # The next preview made clears the expired ones away.
    await _create(as_owner, ws)
    async with sessions() as db:
        left = (await db.scalars(sa.select(DashboardPreview.id))).all()
    assert uuid.UUID(preview["id"]) not in left
    assert len(left) == 1
