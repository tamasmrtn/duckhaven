"""Applying a dashboard file from git, and detaching a dashboard from git.

A file in git is applied with ``PUT …/source``: the body is the file, kept byte
for byte, and the answer is what the apply changed. From then on the dashboard
is changed only by applying its file again, until an owner detaches it.
"""

from __future__ import annotations

import hashlib

import pytest

from . import test_dashboards as dashboards
from .test_dashboards import _create, _member, _save, _sign_in, spec

# The signed-in owner, and a workspace whose `sales` model is published.
owner = dashboards.owner
as_owner = dashboards.as_owner
ws = dashboards.ws

FILE = """\
# Owned by the sales analytics team.
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
      - id: notes
        type: text
        markdown: Revenue is net of refunds.
"""

#: FILE with a new title, a changed and an added parameter, a bar in place of
#: the note, and the kpi moved after it.
CHANGED = """\
version: 1
name: sales-overview
title: Sales numbers
parameters:
  period: {type: time_range, default: last_90_days}
  country: {type: dimension, model: sales, dimension: country}
rows:
  - widgets:
      - id: by_country
        type: bar
        title: Revenue by country
        query: {model: sales, metrics: [revenue], dimensions: [country], time_range: $period}
      - id: revenue
        type: kpi
        title: Revenue
        query: {model: sales, metrics: [revenue], time_range: $period}
"""


async def _apply(client, ws: str, name: str, text: str | bytes, **params):
    query = {k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()}
    return await client.put(
        f"/workspaces/{ws}/dashboards/{name}/source",
        params=query,
        content=text.encode() if isinstance(text, str) else text,
        headers={"Content-Type": "application/yaml"},
    )


def _changes(plan: dict) -> list[tuple[str, str]]:
    return [(c["path"], c["change"]) for c in plan["changes"]]


# ── Applying ──────────────────────────────────────────────────────────────────


async def test_applying_a_file_creates_a_draft_managed_in_git(as_owner, ws):
    resp = await _apply(
        as_owner, ws, "sales-overview", FILE, source_id="acme/bi", source_ref="3f2a1c9"
    )

    assert resp.status_code == 201, resp.text
    plan = resp.json()
    assert (plan["action"], plan["dry_run"], plan["published"]) == ("create", False, False)
    assert plan["current_hash"] is None
    assert _changes(plan) == [
        ("parameters.period", "added"),
        ("widgets.revenue", "added"),
        ("widgets.notes", "added"),
    ]
    board = plan["dashboard"]
    assert (board["managed"], board["status"], board["draft"]) == (True, "draft", True)
    assert (board["source_id"], board["source_ref"]) == ("acme/bi", "3f2a1c9")
    assert board["source_hash"] == hashlib.sha256(FILE.encode()).hexdigest()
    assert plan["spec_hash"] == board["spec_hash"]
    source = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/source?draft=true")
    assert source.text == FILE


async def test_applying_with_publish_publishes_and_says_where_it_came_from(
    as_owner,
    ws,
):
    resp = await _apply(
        as_owner,
        ws,
        "sales-overview",
        FILE,
        publish=True,
        source_id="acme/bi",
        source_ref="3f2a1c9",
    )

    assert resp.status_code == 201, resp.text
    board = resp.json()["dashboard"]
    assert (board["status"], board["has_draft"], board["draft"]) == ("published", False, False)
    revisions = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/revisions")
    assert [r["message"] for r in revisions.json()["items"]] == ["Applied from acme/bi at 3f2a1c9"]
    published = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/source")
    assert published.text == FILE


async def test_a_dry_run_says_what_would_change_and_changes_nothing(as_owner, ws):
    before = (await _apply(as_owner, ws, "sales-overview", FILE, publish=True)).json()

    resp = await _apply(as_owner, ws, "sales-overview", CHANGED, publish=True, dry_run=True)

    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert (plan["action"], plan["dry_run"], plan["dashboard"]) == ("update", True, None)
    assert plan["current_hash"] == before["dashboard"]["spec_hash"] != plan["spec_hash"]
    assert _changes(plan) == [
        ("title", "changed"),
        ("parameters.period", "changed"),
        ("parameters.country", "added"),
        ("widgets.by_country", "added"),
        ("widgets.notes", "removed"),
        ("layout", "changed"),
    ]
    after = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview?draft=true")).json()
    assert (after["title"], after["version"]) == ("Sales", before["dashboard"]["version"])


async def test_a_dry_run_of_a_new_dashboard_creates_nothing(as_owner, ws):
    plan = (await _apply(as_owner, ws, "sales-overview", FILE, dry_run=True)).json()

    assert (plan["action"], plan["dashboard"]) == ("create", None)
    missing = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")
    assert missing.status_code == 404


async def test_the_same_file_again_changes_nothing(as_owner, ws):
    first = await _apply(
        as_owner, ws, "sales-overview", FILE, publish=True, source_id="acme/bi", source_ref="a1"
    )
    board = first.json()["dashboard"]

    again = await _apply(
        as_owner, ws, "sales-overview", FILE, publish=True, source_id="acme/bi", source_ref="b2"
    )

    assert again.status_code == 200
    plan = again.json()
    assert (plan["action"], plan["changes"], plan["published"]) == ("unchanged", [], False)
    assert plan["dashboard"]["version"] == board["version"]
    assert plan["dashboard"]["source_ref"] == "a1"


async def test_a_new_comment_changes_only_the_text(as_owner, ws):
    await _apply(as_owner, ws, "sales-overview", FILE)

    plan = (await _apply(as_owner, ws, "sales-overview", "# Checked by finance.\n" + FILE)).json()

    assert plan["action"] == "update"
    assert _changes(plan) == [("source_text", "changed")]
    assert plan["dashboard"]["source_text"].startswith("# Checked by finance.")


async def test_applying_without_publishing_leaves_readers_the_published_file(
    as_owner,
    ws,
):
    await _apply(as_owner, ws, "sales-overview", FILE, publish=True)

    plan = (await _apply(as_owner, ws, "sales-overview", CHANGED)).json()

    assert plan["dashboard"]["draft"] is True and plan["dashboard"]["title"] == "Sales"
    published = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")).json()
    assert published["spec"]["title"] == "Sales"
    assert published["has_draft"] is True
    # Publishing compares with what readers see, and drops the draft.
    publish = (await _apply(as_owner, ws, "sales-overview", CHANGED, publish=True)).json()
    assert ("title", "changed") in _changes(publish)
    assert publish["dashboard"]["has_draft"] is False


# ── Preconditions ─────────────────────────────────────────────────────────────


async def test_an_apply_expecting_another_file_is_refused_unless_forced(as_owner, ws):
    board = (await _apply(as_owner, ws, "sales-overview", FILE)).json()["dashboard"]

    stale = await _apply(as_owner, ws, "sales-overview", CHANGED, expected_hash="0" * 64)
    assert stale.status_code == 409
    assert stale.json()["error"] == "source_changed"
    assert stale.json()["details"]["current_hash"] == board["spec_hash"]

    fresh = await _apply(as_owner, ws, "sales-overview", CHANGED, expected_hash=board["spec_hash"])
    assert fresh.status_code == 200, fresh.text
    forced = await _apply(as_owner, ws, "sales-overview", FILE, expected_hash="0" * 64, force=True)
    assert forced.status_code == 200, forced.text


async def test_expecting_a_file_where_there_is_none_is_refused(as_owner, ws):
    resp = await _apply(as_owner, ws, "sales-overview", FILE, expected_hash="0" * 64)

    assert resp.status_code == 409
    assert resp.json()["error"] == "source_changed"


async def test_another_repository_takes_a_dashboard_over_only_when_forced(as_owner, ws):
    await _apply(as_owner, ws, "sales-overview", FILE, source_id="acme/bi")

    other = await _apply(as_owner, ws, "sales-overview", CHANGED, source_id="acme/finance")
    assert other.status_code == 409
    assert other.json()["error"] == "source_changed"
    assert "acme/bi" in other.json()["message"]

    forced = await _apply(
        as_owner, ws, "sales-overview", CHANGED, source_id="acme/finance", force=True
    )
    assert forced.json()["dashboard"]["source_id"] == "acme/finance"
    # Left out, the source is kept.
    kept = await _apply(as_owner, ws, "sales-overview", FILE)
    assert kept.json()["dashboard"]["source_id"] == "acme/finance"


async def test_a_dashboard_made_in_duckhaven_is_taken_over_only_when_adopted(
    as_owner,
    ws,
):
    made = await _create(as_owner, ws)

    refused = await _apply(as_owner, ws, "sales-overview", FILE)
    assert refused.status_code == 409
    assert refused.json()["error"] == "dashboard_name_taken"
    assert "adopt=true" in refused.json()["message"]

    adopted = await _apply(as_owner, ws, "sales-overview", FILE, adopt=True, source_id="acme/bi")
    assert adopted.status_code == 200, adopted.text
    plan = adopted.json()
    assert (plan["action"], plan["adopted"]) == ("update", True)
    assert plan["dashboard"]["managed"] is True
    assert plan["current_hash"] == made["spec_hash"]
    edit = await _save(as_owner, ws, "sales-overview", plan["dashboard"]["version"], spec())
    assert edit.status_code == 409
    assert edit.json()["error"] == "managed_dashboard"


# ── The file ──────────────────────────────────────────────────────────────────


async def test_a_new_dashboard_is_applied_under_its_own_name(as_owner, ws):
    resp = await _apply(as_owner, ws, "sales", FILE)

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_dashboard"
    [problem] = body["details"]["diagnostics"]
    assert (problem["path"], problem["line"]) == ("name", 3)
    assert "'sales-overview'" in problem["message"]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (FILE.replace("type: kpi", "type: gauge"), "invalid_dashboard"),
        (FILE.replace("version: 1", "version: 9"), "unsupported_spec_version"),
        (b"\xff\xfeversion: 1\n", "invalid_dashboard"),
    ],
)
async def test_a_file_that_does_not_load_is_refused_and_nothing_is_stored(
    as_owner,
    ws,
    text,
    code,
):
    resp = await _apply(as_owner, ws, "sales-overview", text)

    assert resp.status_code == 422
    assert resp.json()["error"] == code
    missing = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")
    assert missing.status_code == 404


async def test_a_draft_may_hold_what_only_publishing_refuses(as_owner, ws):
    """As a draft saved in DuckHaven may: a metric nobody has defined yet, say."""
    resp = await _apply(as_owner, ws, "sales-overview", FILE.replace("[revenue]", "[margin]"))

    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("dry_run", [True, False])
async def test_publishing_checks_the_file_against_the_workspace_first(
    as_owner,
    ws,
    dry_run,
):
    bad = FILE.replace("[revenue]", "[margin]")

    resp = await _apply(as_owner, ws, "sales-overview", bad, publish=True, dry_run=dry_run)

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_dashboard"
    assert body["details"]["diagnostics"][0]["line"] == 12
    missing = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")
    assert missing.status_code == 404


async def test_a_new_name_renames_the_dashboard_when_it_is_published(as_owner, ws):
    await _apply(as_owner, ws, "sales-overview", FILE, publish=True)
    renamed = FILE.replace("name: sales-overview", "name: sales")

    draft = (await _apply(as_owner, ws, "sales-overview", renamed)).json()
    assert (draft["name"], draft["renamed_from"]) == ("sales-overview", None)
    assert ("name", "changed") in _changes(draft)

    plan = (await _apply(as_owner, ws, "sales-overview", renamed, publish=True)).json()
    assert (plan["name"], plan["renamed_from"]) == ("sales", "sales-overview")
    old = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")
    assert old.status_code == 200 and old.json()["name"] == "sales"


# ── Roles ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "params", "code"),
    [
        ("reader", {}, 403),
        ("reader", {"dry_run": True}, 403),
        ("writer", {}, 201),
        ("writer", {"dry_run": True}, 200),
        ("writer", {"publish": True}, 403),
        # A dry run changes nothing, so a pull request's check needs no owner.
        ("writer", {"publish": True, "dry_run": True}, 200),
        ("writer", {"adopt": True}, 403),
    ],
)
async def test_a_writer_applies_drafts_and_an_owner_publishes(
    client,
    as_owner,
    ws,
    db_session,
    role,
    params,
    code,
):
    await _member(db_session, ws, role)
    await _sign_in(client, role)

    resp = await _apply(client, ws, "sales-overview", FILE, **params)

    assert resp.status_code == code, resp.text


# ── Detaching ─────────────────────────────────────────────────────────────────


async def test_detaching_hands_a_dashboard_back_to_duckhaven(as_owner, ws):
    applied = await _apply(
        as_owner, ws, "sales-overview", FILE, publish=True, source_id="acme/bi", source_ref="a1"
    )
    board = applied.json()["dashboard"]

    resp = await as_owner.delete(f"/workspaces/{ws}/dashboards/sales-overview/source")

    assert resp.status_code == 204
    after = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")).json()
    assert (after["managed"], after["source"]) == (False, "ui")
    assert (after["source_id"], after["source_ref"], after["source_hash"]) == (None, None, None)
    assert after["version"] == board["version"] + 1
    # The file stays as it was written.
    source = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/source")
    assert source.text == FILE
    edit = await _save(as_owner, ws, "sales-overview", after["version"], spec())
    assert edit.status_code == 200, edit.text
    # A dashboard not managed in git has nothing to detach.
    again = await as_owner.delete(f"/workspaces/{ws}/dashboards/sales-overview/source")
    assert again.status_code == 204


async def test_only_an_owner_detaches(client, as_owner, ws, db_session):
    await _apply(as_owner, ws, "sales-overview", FILE, publish=True)
    await _member(db_session, ws, "writer")
    await _sign_in(client, "writer")

    resp = await client.delete(f"/workspaces/{ws}/dashboards/sales-overview/source")

    assert resp.status_code == 403
