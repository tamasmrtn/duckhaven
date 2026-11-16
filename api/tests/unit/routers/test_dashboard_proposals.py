"""Proposals: changes to a dashboard, proposed for one person to accept as its draft.

What carries the weight: nothing changes until the proposal is accepted, a
proposal never overwrites an edit made after it, and it is checked as
publishing checks a file before anyone sees it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from api.models.dashboard import DashboardProposal
from api.models.user import Credential, User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import generate_pat, hash_token

from . import test_dashboard_run as runs
from .test_dashboard_run import _kpi, spec
from .test_dashboards import _member, _sign_in

# The run tests' file database, signed-in owner, and workspace with a `sales` model.
db_engine = runs.db_engine
sessions = runs.sessions
owner = runs.owner
as_owner = runs.as_owner
ws = runs.ws

NAME = "sales-overview"


def _proposals(ws: str) -> str:
    return f"/workspaces/{ws}/dashboard-proposals"


async def _dashboard(client, ws: str, *widgets: dict) -> dict:
    resp = await client.post(
        f"/workspaces/{ws}/dashboards", json={"spec": spec(*(widgets or (_kpi("revenue"),)))}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _propose(client, ws: str, *operations: dict, summary: str = "Add orders.") -> dict:
    resp = await client.post(
        _proposals(ws),
        json={"dashboard": NAME, "summary": summary, "operations": list(operations)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


ADD_ORDERS = {"op": "add_widget", "widget": _kpi("orders", "orders"), "row": 0}


async def test_a_proposal_changes_nothing_until_it_is_accepted(as_owner, ws):
    board = await _dashboard(as_owner, ws)

    proposal = await _propose(as_owner, ws, ADD_ORDERS, {"op": "set_metadata", "title": "Sales!"})

    assert proposal["status"] == "pending"
    assert proposal["dashboard"] == NAME and proposal["title"] == "Sales!"
    assert proposal["changes"] == [
        {"path": "title", "change": "changed"},
        {"path": "widgets.orders", "change": "added"},
        {"path": "layout", "change": "changed"},
    ]
    assert proposal["base_version"] == board["version"]
    assert proposal["base_hash"] == board["spec_hash"]
    assert proposal["managed"] is False
    assert "id: orders" in proposal["result_source"]
    draft = (await as_owner.get(f"/workspaces/{ws}/dashboards/{NAME}?draft=true")).json()
    assert draft["version"] == board["version"] and draft["title"] == "Sales"

    resp = await as_owner.post(f"{_proposals(ws)}/{proposal['id']}/accept")

    assert resp.status_code == 200, resp.text
    accepted = resp.json()
    assert accepted["draft"] is True and accepted["version"] == board["version"] + 1
    assert accepted["spec_hash"] == proposal["result_hash"]
    assert accepted["updated_via"] == "assistant"
    # Written as canonical YAML, which the editor shows.
    assert accepted["source_text"] == proposal["result_source"]
    # Never published, so the title follows the draft at once.
    assert accepted["title"] == "Sales!"
    read = (await as_owner.get(f"{_proposals(ws)}/{proposal['id']}")).json()
    assert read["status"] == "accepted" and read["decided_at"]
    again = await as_owner.post(f"{_proposals(ws)}/{proposal['id']}/accept")
    assert again.status_code == 409 and again.json()["error"] == "proposal_decided"


async def test_a_proposal_applies_to_the_draft_and_leaves_readers_the_published_file(as_owner, ws):
    board = await _dashboard(as_owner, ws)
    base = f"/workspaces/{ws}/dashboards/{NAME}"
    published = await as_owner.post(f"{base}/publish", json={"base_version": board["version"]})
    assert published.status_code == 200, published.text
    saved = await as_owner.patch(
        base,
        json={
            "spec": spec(_kpi("revenue"), _kpi("count", "orders")),
            "base_version": published.json()["version"],
        },
    )
    assert saved.status_code == 200, saved.text

    proposal = await _propose(as_owner, ws, {"op": "remove_widget", "id": "count"})
    accepted = await as_owner.post(f"{_proposals(ws)}/{proposal['id']}/accept")

    assert proposal["changes"] == [
        {"path": "widgets.count", "change": "removed"},
        {"path": "layout", "change": "changed"},
    ]
    assert accepted.status_code == 200, accepted.text
    shown = (await as_owner.get(base)).json()
    assert shown["draft"] is False and shown["spec_hash"] == published.json()["spec_hash"]


async def test_a_proposal_that_cannot_apply_is_refused_with_what_to_fix(as_owner, ws):
    await _dashboard(as_owner, ws)

    async def refused(*operations):
        resp = await as_owner.post(
            _proposals(ws),
            json={"dashboard": NAME, "summary": "Change it.", "operations": list(operations)},
        )
        assert resp.status_code == 422, resp.text
        return resp.json()

    missing = await refused(ADD_ORDERS, {"op": "remove_widget", "id": "nope"})
    assert missing["error"] == "invalid_proposal"
    assert missing["message"] == "Operation 2: The dashboard has no widget 'nope'."
    assert missing["details"]["operation"] == 1

    same = await refused({"op": "set_metadata", "title": "Sales"})
    assert same["error"] == "invalid_proposal" and "change nothing" in same["message"]

    too_wide = await refused({**ADD_ORDERS, "widget": {**_kpi("orders", "orders"), "width": 13}})
    assert too_wide["error"] == "invalid_dashboard"
    assert too_wide["details"]["diagnostics"][0]["path"].startswith("rows[0].widgets[1]")

    unknown = await refused({**ADD_ORDERS, "widget": _kpi("profit", "profit")})
    assert unknown["error"] == "invalid_dashboard"
    assert "profit" in unknown["details"]["diagnostics"][0]["message"]

    shape = await as_owner.post(
        _proposals(ws),
        json={"dashboard": NAME, "summary": "x", "operations": [{"op": "publish"}]},
    )
    assert shape.status_code == 422
    assert (await as_owner.get(_proposals(ws) + f"/{uuid.uuid4()}")).status_code == 404


async def test_a_problem_the_dashboard_had_already_does_not_stop_a_proposal(as_owner, ws):
    # A draft may read a metric that is gone; publishing is what refuses it.
    await _dashboard(as_owner, ws, _kpi("old", "profit"))

    proposal = await _propose(as_owner, ws, ADD_ORDERS)

    assert proposal["changes"][0] == {"path": "widgets.orders", "change": "added"}


async def test_a_proposal_made_before_an_edit_is_stale_and_overwrites_nothing(as_owner, ws):
    board = await _dashboard(as_owner, ws)
    proposal = await _propose(as_owner, ws, ADD_ORDERS)
    edited = await as_owner.patch(
        f"/workspaces/{ws}/dashboards/{NAME}",
        json={"spec": spec(_kpi("revenue"), _kpi("mine")), "base_version": board["version"]},
    )
    assert edited.status_code == 200, edited.text

    read = (await as_owner.get(f"{_proposals(ws)}/{proposal['id']}")).json()
    resp = await as_owner.post(f"{_proposals(ws)}/{proposal['id']}/accept")

    assert read["status"] == "stale"
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "proposal_stale"
    assert resp.json()["details"]["current"]["version"] == edited.json()["version"]
    named = edited.json()["updated_by_name"]
    assert named and resp.json()["details"]["current"]["updated_by_name"] == named
    draft = (await as_owner.get(f"/workspaces/{ws}/dashboards/{NAME}?draft=true")).json()
    assert draft["spec_hash"] == edited.json()["spec_hash"]


async def test_a_proposal_expires_a_day_after_it_was_made(as_owner, ws, sessions):
    await _dashboard(as_owner, ws)
    proposal = await _propose(as_owner, ws, ADD_ORDERS)
    made = datetime.fromisoformat(proposal["created_at"])
    assert datetime.fromisoformat(proposal["expires_at"]) - made == timedelta(days=1)
    async with sessions() as db:
        row = await db.get(DashboardProposal, uuid.UUID(proposal["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    read = (await as_owner.get(f"{_proposals(ws)}/{proposal['id']}")).json()
    resp = await as_owner.post(f"{_proposals(ws)}/{proposal['id']}/accept")

    assert read["status"] == "expired"
    assert resp.status_code == 410 and resp.json()["error"] == "proposal_expired"
    # The next proposal made clears the expired ones away.
    await _propose(as_owner, ws, ADD_ORDERS)
    async with sessions() as db:
        left = (await db.scalars(sa.select(DashboardProposal.id))).all()
    assert uuid.UUID(proposal["id"]) not in left and len(left) == 1


async def test_a_dashboard_managed_in_git_takes_proposals_but_not_their_acceptance(as_owner, ws):
    resp = await as_owner.put(
        f"/workspaces/{ws}/dashboards/{NAME}/source",
        params={"source_id": "acme/bi"},
        content=YAML.encode(),
        headers={"Content-Type": "application/yaml"},
    )
    assert resp.status_code in (200, 201), resp.text

    proposal = await _propose(as_owner, ws, ADD_ORDERS)
    accept = await as_owner.post(f"{_proposals(ws)}/{proposal['id']}/accept")

    assert proposal["managed"] is True
    assert accept.status_code == 409 and accept.json()["error"] == "managed_dashboard"


async def test_discarding_a_proposal_leaves_the_dashboard_as_it_is(as_owner, ws):
    board = await _dashboard(as_owner, ws)
    proposal = await _propose(as_owner, ws, ADD_ORDERS)
    url = f"{_proposals(ws)}/{proposal['id']}"

    first, second = await as_owner.delete(url), await as_owner.delete(url)

    assert (first.status_code, second.status_code) == (204, 204)
    assert (await as_owner.get(url)).json()["status"] == "discarded"
    refused = await as_owner.post(f"{url}/accept")
    assert refused.status_code == 409 and refused.json()["error"] == "proposal_decided"
    draft = (await as_owner.get(f"/workspaces/{ws}/dashboards/{NAME}?draft=true")).json()
    assert draft["version"] == board["version"]

    accepted = await _propose(as_owner, ws, ADD_ORDERS)
    await as_owner.post(f"{_proposals(ws)}/{accepted['id']}/accept")
    late = await as_owner.delete(f"{_proposals(ws)}/{accepted['id']}")
    assert late.status_code == 409 and late.json()["error"] == "proposal_decided"


async def test_only_its_person_can_read_or_decide_a_proposal(as_owner, ws, client, db_session):
    await _dashboard(as_owner, ws)
    proposal = await _propose(as_owner, ws, ADD_ORDERS)
    url = f"{_proposals(ws)}/{proposal['id']}"
    await _member(db_session, ws, "writer")
    await _member(db_session, ws, "reader")

    await _sign_in(client, "writer")
    other = [await client.get(url), await client.post(f"{url}/accept"), await client.delete(url)]
    await _sign_in(client, "reader")
    body = {"dashboard": NAME, "summary": "x", "operations": [ADD_ORDERS]}
    reader = [await client.post(_proposals(ws), json=body), await client.get(url)]

    assert [r.status_code for r in other] == [404, 404, 404]
    assert [r.status_code for r in reader] == [403, 403]


async def test_a_proposal_the_assistant_makes_is_the_persons_it_acted_for(
    as_owner, ws, client, owner, db_session
):
    """The assistant proposes with a token for its own account, acting for the person
    who asked. The person decides the proposal; the assistant cannot read it back."""
    await _dashboard(as_owner, ws)
    account = User(
        email="assistant@service-account.local",
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add(account)
    await db_session.commit()
    workspace = (
        await db_session.execute(sa.select(Workspace).where(Workspace.slug == ws))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=account.id, role="writer"))
    token = generate_pat()
    db_session.add(
        Credential(
            user_id=account.id,
            on_behalf_of_id=owner.id,
            kind="pat",
            token_hash=hash_token(token),
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )
    )
    await db_session.commit()
    assistant = {"Authorization": f"Bearer {token}"}

    client.cookies.clear()
    made = await client.post(
        _proposals(ws),
        json={"dashboard": NAME, "summary": "Add orders.", "operations": [ADD_ORDERS]},
        headers=assistant,
    )
    assert made.status_code == 201, made.text
    url = f"{_proposals(ws)}/{made.json()['id']}"
    by_assistant = [
        await client.get(url, headers=assistant),
        await client.post(f"{url}/accept", headers=assistant),
        await client.delete(url, headers=assistant),
    ]

    assert [r.status_code for r in by_assistant] == [404, 404, 404]
    row = (
        await db_session.execute(
            sa.select(DashboardProposal).where(DashboardProposal.id == uuid.UUID(made.json()["id"]))
        )
    ).scalar_one()
    assert row.created_by == owner.id
    await client.post("/auth/login", json={"email": "owner@d.local", "password": "pw"})
    assert (await client.get(url)).json()["status"] == "pending"
    accepted = await client.post(f"{url}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["spec_hash"] == made.json()["result_hash"]


YAML = """\
version: 1
name: sales-overview
title: Sales
parameters:
  period: {type: time_range, default: last_30_days}
rows:
  - widgets:
      - id: revenue
        type: kpi
        title: Revenue
        query: {model: sales, metrics: [revenue], time_range: $period}
"""
