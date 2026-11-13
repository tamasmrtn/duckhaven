"""Stored dashboards: drafts, publishing, history, names and who may do what.

Reading is ``reader``, editing a draft is ``writer``, and publishing and deleting
are ``owner``. Readers see only what was published: a draft never leaks to them,
not even as a 404 that differs from a dashboard that does not exist.
"""

from __future__ import annotations

import copy

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from api.models.dashboard import Dashboard, DashboardAlias
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import hash_password
from api.services.dashboards.loader import load

from .test_semantic import build_model

SQL = "SELECT id, total_amount FROM warehouse.analytics.orders WHERE order_date >= $since"

SPEC = {
    "version": 1,
    "name": "sales-overview",
    "title": "Sales",
    "labels": ["sales"],
    "parameters": {
        "period": {"type": "time_range", "default": "last_30_days"},
        "country": {"type": "dimension", "model": "sales", "dimension": "country"},
    },
    "rows": [
        {
            "widgets": [
                {
                    "id": "revenue",
                    "type": "kpi",
                    "title": "Revenue",
                    "query": {"model": "sales", "metrics": ["revenue"], "time_range": "$period"},
                },
                {
                    "id": "by_country",
                    "type": "bar",
                    "title": "By country",
                    "query": {
                        "model": "sales",
                        "metrics": ["revenue"],
                        "dimensions": ["country"],
                        "time_range": "$period",
                    },
                },
                {
                    "id": "orders",
                    "type": "table",
                    "title": "Orders",
                    "sql": SQL,
                    "params": {"since": "$period.start"},
                },
            ]
        }
    ],
}

YAML = """\
# The team's numbers.
version: 1
name: sales-overview
title: Sales
rows:
  - widgets:
      - id: notes
        type: text
        markdown: Revenue is net of refunds.
"""


def spec(**changes) -> dict:
    out = copy.deepcopy(SPEC)
    out.update(changes)
    return out


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="owner@d.local", password_hash=hash_password("pw"), name="Owner", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def as_owner(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@d.local", "password": "pw"})
    return client


@pytest.fixture
async def ws(as_owner, owner, db_session) -> str:
    """A workspace whose `sales` model and its metric are published, as dashboards need."""
    from api.models.storage_backend import StorageBackend

    from .test_semantic import CUSTOMER_COLUMNS, ORDER_COLUMNS

    slug = (await as_owner.post("/workspaces", json={"slug": "dash", "name": "Dash"})).json()[
        "slug"
    ]
    backend = StorageBackend(kind="object_store", name="p", root_uri="", created_by=owner.id)
    db_session.add(backend)
    await db_session.commit()
    await db_session.refresh(backend)
    await as_owner.post(
        f"/workspaces/{slug}/catalogs",
        json={"name": "warehouse", "storage_backend_id": str(backend.id)},
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
    published = await as_owner.post(f"/workspaces/{slug}/semantic/models/sales/publish")
    assert published.status_code == 200, published.text
    metric = await as_owner.patch(
        f"/workspaces/{slug}/semantic/models/sales/metrics/revenue", json={"status": "published"}
    )
    assert metric.status_code == 200, metric.text
    return slug


async def _member(db_session, ws: str, role: str) -> None:
    user = User(email=f"{role}@d.local", password_hash=hash_password("pw"), name=role, role="user")
    db_session.add(user)
    await db_session.commit()
    workspace = (
        await db_session.execute(sa.select(Workspace).where(Workspace.slug == ws))
    ).scalar_one()
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role))
    await db_session.commit()


async def _sign_in(client: AsyncClient, role: str) -> None:
    client.cookies.clear()
    resp = await client.post("/auth/login", json={"email": f"{role}@d.local", "password": "pw"})
    assert resp.status_code == 200


async def _create(client: AsyncClient, ws: str, body: dict | None = None) -> dict:
    resp = await client.post(f"/workspaces/{ws}/dashboards", json={"spec": body or spec()})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _publish(client: AsyncClient, ws: str, name: str, version: int, **body) -> dict:
    resp = await client.post(
        f"/workspaces/{ws}/dashboards/{name}/publish", json={"base_version": version, **body}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _save(client: AsyncClient, ws: str, name: str, version: int, body: dict):
    return await client.patch(
        f"/workspaces/{ws}/dashboards/{name}", json={"base_version": version, "spec": body}
    )


# ── Creating and reading ──────────────────────────────────────────────────────


async def test_a_new_dashboard_is_a_draft_with_what_it_reads(as_owner, ws):
    body = await _create(as_owner, ws)

    assert body["name"] == "sales-overview"
    assert (body["status"], body["draft"], body["has_draft"], body["version"]) == (
        "draft",
        True,
        True,
        1,
    )
    assert body["labels"] == ["sales"]
    assert body["managed"] is False
    assert body["updated_via"] == "ui"
    assert {(r["kind"], r["model"], r["name"]) for r in body["refs"]} == {
        ("model", None, "sales"),
        ("metric", "sales", "revenue"),
        ("dimension", "sales", "country"),
        ("table", None, "warehouse.analytics.orders"),
    }
    assert len(body["spec_hash"]) == 64


async def test_a_file_sent_as_yaml_is_kept_as_written(as_owner, ws):
    resp = await as_owner.post(f"/workspaces/{ws}/dashboards", json={"source_text": YAML})
    assert resp.status_code == 201
    assert resp.json()["source_text"] == YAML

    source = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/source")
    assert source.headers["content-type"].startswith("application/yaml")
    assert source.text == YAML


async def test_a_file_sent_as_json_exports_as_yaml_that_loads_back(as_owner, ws):
    created = await _create(as_owner, ws)

    source = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/source")

    assert "sales-overview.dashboard.yml" in source.headers["content-disposition"]
    assert load(source.text).spec.model_dump(exclude_defaults=True, mode="json") == created["spec"]


async def test_a_file_must_come_as_exactly_one_of_spec_or_source_text(as_owner, ws):
    both = await as_owner.post(
        f"/workspaces/{ws}/dashboards", json={"spec": spec(), "source_text": YAML}
    )
    neither = await as_owner.post(f"/workspaces/{ws}/dashboards", json={})

    assert both.status_code == neither.status_code == 422


async def test_an_invalid_file_lists_every_problem_with_its_place(as_owner, ws):
    text = YAML.replace("type: text", "type: text\n        widht: 4").replace(
        "title: Sales", "title: 5"
    )

    resp = await as_owner.post(f"/workspaces/{ws}/dashboards", json={"source_text": text})

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_dashboard"
    assert body["message"].startswith("2 problems")
    found = {d["path"]: d for d in body["details"]["diagnostics"]}
    assert found["rows[0].widgets[0].widht"]["line"] == 9
    assert "title" in found


async def test_a_file_for_another_format_version_says_so(as_owner, ws):
    resp = await as_owner.post(f"/workspaces/{ws}/dashboards", json={"spec": spec(version=2)})

    assert resp.status_code == 422
    assert resp.json()["error"] == "unsupported_spec_version"


async def test_a_file_over_the_size_limit_is_refused(as_owner, ws):
    resp = await as_owner.post(
        f"/workspaces/{ws}/dashboards", json={"spec": spec(description="x" * 300_000)}
    )

    assert resp.status_code == 422
    assert "at most 256 KB" in resp.json()["message"]


async def test_names_are_unique_in_a_workspace(as_owner, ws):
    await _create(as_owner, ws)

    again = await as_owner.post(f"/workspaces/{ws}/dashboards", json={"spec": spec()})

    assert again.status_code == 409
    assert again.json()["error"] == "dashboard_name_taken"


async def test_a_missing_dashboard_is_a_404(as_owner, ws):
    resp = await as_owner.get(f"/workspaces/{ws}/dashboards/nope")

    assert resp.status_code == 404


# ── Drafts and publishing ─────────────────────────────────────────────────────


async def test_readers_see_only_what_was_published(client, as_owner, ws, db_session):
    await _create(as_owner, ws)
    await _member(db_session, ws, "reader")
    await _sign_in(client, "reader")

    assert (await client.get(f"/workspaces/{ws}/dashboards/sales-overview")).status_code == 404
    assert (await client.get(f"/workspaces/{ws}/dashboards")).json()["items"] == []

    await _sign_in(client, "owner")
    await _publish(client, ws, "sales-overview", 1)
    await _save(client, ws, "sales-overview", 2, spec(title="Sales, renamed"))
    await _sign_in(client, "reader")

    body = (await client.get(f"/workspaces/{ws}/dashboards/sales-overview")).json()
    assert (body["title"], body["spec"]["title"], body["draft"]) == ("Sales", "Sales", False)
    listed = (await client.get(f"/workspaces/{ws}/dashboards")).json()["items"]
    assert [(d["name"], d["title"], d["has_draft"]) for d in listed] == [
        ("sales-overview", "Sales", True)
    ]


async def test_writers_read_the_draft_when_they_ask_for_it(as_owner, ws):
    await _create(as_owner, ws)
    await _publish(as_owner, ws, "sales-overview", 1)
    await _save(as_owner, ws, "sales-overview", 2, spec(title="Next"))

    published = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")).json()
    draft = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview?draft=true")).json()

    assert (published["spec"]["title"], published["draft"]) == ("Sales", False)
    assert (draft["spec"]["title"], draft["draft"]) == ("Next", True)
    assert published["spec_hash"] != draft["spec_hash"]


async def test_publishing_moves_the_draft_and_records_a_revision(as_owner, ws, owner):
    await _create(as_owner, ws)

    body = await _publish(as_owner, ws, "sales-overview", 1, message="First cut")

    assert (body["status"], body["has_draft"], body["draft"], body["version"]) == (
        "published",
        False,
        False,
        2,
    )
    assert body["published_by"] == str(owner.id)
    revisions = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/revisions")).json()
    assert [(r["number"], r["message"]) for r in revisions["items"]] == [(1, "First cut")]
    assert revisions["items"][0]["spec_hash"] == body["spec_hash"]


async def test_each_publish_is_kept_newest_first(as_owner, ws):
    await _create(as_owner, ws)
    await _publish(as_owner, ws, "sales-overview", 1)
    await _save(as_owner, ws, "sales-overview", 2, spec(title="Second"))
    await _publish(as_owner, ws, "sales-overview", 3, message="Retitled")

    listed = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/revisions")).json()
    first = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/revisions/1")
    missing = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/revisions/9")

    assert [r["number"] for r in listed["items"]] == [2, 1]
    assert first.json()["spec"]["title"] == "Sales"
    assert missing.status_code == 404


async def test_publishing_with_no_draft_changes_nothing(as_owner, ws):
    await _create(as_owner, ws)
    await _publish(as_owner, ws, "sales-overview", 1)

    again = await _publish(as_owner, ws, "sales-overview", 2)

    assert again["version"] == 2
    revisions = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview/revisions")).json()
    assert len(revisions["items"]) == 1


@pytest.mark.parametrize(
    ("change", "path", "message"),
    [
        (
            lambda s: s["rows"][0]["widgets"][0]["query"].update(metrics=["profit"]),
            "rows[0].widgets[0].query",
            "no metric called 'profit'",
        ),
        (
            lambda s: s["parameters"]["country"].update(dimension="planet"),
            "parameters.country.dimension",
            "no dimension 'planet'",
        ),
        (
            lambda s: s["rows"][0]["widgets"][1]["query"].update(model="nope"),
            "rows[0].widgets[1].query.model",
            "no semantic model 'nope'",
        ),
        (
            lambda s: s["rows"][0]["widgets"][2].update(sql="DELETE FROM t"),
            "rows[0].widgets[2].sql",
            "one SELECT statement",
        ),
        (
            lambda s: s["rows"][0]["widgets"][2].update(sql="SELEC nonsense"),
            "rows[0].widgets[2].sql",
            "SQL parse error",
        ),
        (
            lambda s: s["rows"][0]["widgets"][2].update(sql="SELECT * FROM read_csv('/etc/x')"),
            "rows[0].widgets[2].sql",
            "is not permitted",
        ),
        (
            lambda s: s["parameters"]["country"].update(model="nope"),
            "parameters.country.model",
            "no semantic model 'nope'",
        ),
        (
            # A set dimension parameter asks whether each metric reaches it.
            lambda s: (
                s["parameters"]["country"].update(default="DE"),
                s["rows"][0]["widgets"][0]["query"].update(metrics=["profit"]),
            ),
            "rows[0].widgets[0].query",
            "no metric called 'profit'",
        ),
        (
            lambda s: (
                s["parameters"]["period"].update(
                    default={"kind": "trailing", "grain": "month", "n": 24}
                ),
                s["rows"][0]["widgets"][0]["query"].update(compare="previous_year"),
            ),
            "rows[0].widgets[0]",
            "overlap",
        ),
    ],
)
async def test_publishing_checks_the_draft_against_the_workspace(
    as_owner, ws, change, path, message
):
    """A draft may name a metric that does not exist yet; a published file may not."""
    body = spec()
    change(body)
    await _create(as_owner, ws, body)

    resp = await as_owner.post(
        f"/workspaces/{ws}/dashboards/sales-overview/publish", json={"base_version": 1}
    )

    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_dashboard"
    found = {d["path"]: d["message"] for d in resp.json()["details"]["diagnostics"]}
    assert message in found[path]


async def test_dashboards_read_published_models_only(as_owner, ws):
    await as_owner.post(f"/workspaces/{ws}/semantic/models", json={"slug": "web", "name": "Web"})
    body = spec()
    body["rows"][0]["widgets"][0]["query"]["model"] = "web"
    await _create(as_owner, ws, body)

    resp = await as_owner.post(
        f"/workspaces/{ws}/dashboards/sales-overview/publish", json={"base_version": 1}
    )

    found = {d["path"]: d["message"] for d in resp.json()["details"]["diagnostics"]}
    assert (
        "'web' is draft; dashboards read published models only"
        in found["rows[0].widgets[0].query.model"]
    )


async def test_discarding_the_draft_goes_back_to_the_published_file(as_owner, ws):
    await _create(as_owner, ws)
    await _publish(as_owner, ws, "sales-overview", 1)
    await _save(as_owner, ws, "sales-overview", 2, spec(title="Abandoned"))

    resp = await as_owner.delete(f"/workspaces/{ws}/dashboards/sales-overview/draft")
    again = await as_owner.delete(f"/workspaces/{ws}/dashboards/sales-overview/draft")

    assert resp.status_code == again.status_code == 204
    body = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview?draft=true")).json()
    assert (body["spec"]["title"], body["draft"], body["has_draft"], body["version"]) == (
        "Sales",
        False,
        False,
        4,
    )


async def test_a_dashboard_never_published_has_no_draft_to_discard(as_owner, ws):
    await _create(as_owner, ws)

    resp = await as_owner.delete(f"/workspaces/{ws}/dashboards/sales-overview/draft")

    assert resp.status_code == 409
    assert resp.json()["error"] == "dashboard_unpublished"


async def test_deleting_a_dashboard_removes_it(as_owner, ws):
    await _create(as_owner, ws)

    assert (await as_owner.delete(f"/workspaces/{ws}/dashboards/sales-overview")).status_code == 204
    assert (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")).status_code == 404


# ── Concurrent edits ──────────────────────────────────────────────────────────


async def test_a_save_against_an_old_version_is_a_conflict_with_the_current_draft(as_owner, ws):
    await _create(as_owner, ws)
    await _save(as_owner, ws, "sales-overview", 1, spec(title="Mine"))

    resp = await _save(as_owner, ws, "sales-overview", 1, spec(title="Theirs"))

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "dashboard_conflict"
    assert body["details"]["current"]["version"] == 2
    assert body["details"]["current"]["spec"]["title"] == "Mine"


async def test_publishing_an_old_version_is_a_conflict(as_owner, ws):
    await _create(as_owner, ws)
    await _save(as_owner, ws, "sales-overview", 1, spec(title="Newer"))

    resp = await as_owner.post(
        f"/workspaces/{ws}/dashboards/sales-overview/publish", json={"base_version": 1}
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == "dashboard_conflict"


# ── Names ─────────────────────────────────────────────────────────────────────


async def test_renaming_a_draft_renames_it_at_once_and_the_old_name_still_finds_it(as_owner, ws):
    await _create(as_owner, ws)

    saved = await _save(as_owner, ws, "sales-overview", 1, spec(name="sales"))

    assert saved.json()["name"] == "sales"
    by_old = await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")
    assert by_old.status_code == 200
    assert by_old.json()["name"] == "sales"


async def test_renaming_a_published_dashboard_waits_for_publish(as_owner, ws):
    await _create(as_owner, ws)
    await _publish(as_owner, ws, "sales-overview", 1)

    saved = await _save(as_owner, ws, "sales-overview", 2, spec(name="sales"))
    assert saved.json()["name"] == "sales-overview"

    published = await _publish(as_owner, ws, "sales-overview", 3)
    assert published["name"] == "sales"
    assert (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")).json()[
        "name"
    ] == "sales"


async def test_a_rename_to_a_taken_name_is_refused_when_saved(as_owner, ws):
    await _create(as_owner, ws)
    await _create(as_owner, ws, spec(name="other"))
    await _publish(as_owner, ws, "sales-overview", 1)

    resp = await _save(as_owner, ws, "sales-overview", 2, spec(name="other"))

    assert resp.status_code == 409
    assert resp.json()["error"] == "dashboard_name_taken"


async def test_a_live_name_wins_over_an_old_one(as_owner, ws, db_session):
    await _create(as_owner, ws)
    await _save(as_owner, ws, "sales-overview", 1, spec(name="sales"))

    await _create(as_owner, ws)  # a new dashboard takes the old name

    body = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")).json()
    assert body["version"] == 1
    aliases = (await db_session.execute(sa.select(DashboardAlias))).scalars().all()
    assert aliases == []


# ── What a dashboard reads ────────────────────────────────────────────────────


async def test_the_list_finds_the_dashboards_that_read_a_model_or_metric(as_owner, ws):
    await _create(as_owner, ws)
    notes = {
        "version": 1,
        "name": "notes",
        "title": "Notes",
        "rows": [{"widgets": [{"id": "n", "type": "text", "markdown": "Hi."}]}],
    }
    await _create(as_owner, ws, notes)

    async def names(**params) -> list[str]:
        resp = await as_owner.get(f"/workspaces/{ws}/dashboards", params=params)
        return [d["name"] for d in resp.json()["items"]]

    assert await names() == ["notes", "sales-overview"]
    assert await names(uses="sales") == ["sales-overview"]
    assert await names(uses="sales.revenue") == ["sales-overview"]
    assert await names(uses="sales.country") == ["sales-overview"]
    assert await names(uses="sales.profit") == []
    assert await names(q="note") == ["notes"]
    assert await names(status="draft") == ["notes", "sales-overview"]
    assert await names(status="published") == []
    assert await names(source="ui") == ["notes", "sales-overview"]
    assert await names(source="git") == await names(source_id="acme/bi") == []


# ── Validation without storing ────────────────────────────────────────────────


async def test_validating_reports_what_publishing_would_refuse(as_owner, ws):
    good = await as_owner.post(f"/workspaces/{ws}/dashboard-validations", json={"spec": spec()})
    bad = spec()
    bad["rows"][0]["widgets"][0]["query"]["metrics"] = ["profit"]
    refused = await as_owner.post(f"/workspaces/{ws}/dashboard-validations", json={"spec": bad})

    assert good.status_code == 200
    assert good.json()["valid"] is True
    assert {r["kind"] for r in good.json()["refs"]} == {"model", "metric", "dimension", "table"}
    assert refused.json()["valid"] is False
    assert [e["path"] for e in refused.json()["errors"]] == ["rows[0].widgets[0].query"]
    assert (await as_owner.get(f"/workspaces/{ws}/dashboards")).json()["items"] == []


async def test_validating_yaml_places_each_problem_on_its_line(as_owner, ws):
    text = YAML.replace("markdown: Revenue is net of refunds.", "markdown: Hi\n    widgets: 1")

    resp = await as_owner.post(
        f"/workspaces/{ws}/dashboard-validations", json={"source_text": text}
    )

    body = resp.json()
    assert body["valid"] is False
    assert body["errors"][0]["line"] is not None


async def test_validating_yaml_places_workspace_problems_on_their_line(as_owner, ws):
    text = YAML.replace(
        "      - id: notes\n        type: text\n        markdown: Revenue is net of refunds.\n",
        "      - id: total\n        type: kpi\n        title: Total\n"
        "        query:\n          model: sales\n          metrics: [profit]\n",
    )

    body = (
        await as_owner.post(f"/workspaces/{ws}/dashboard-validations", json={"source_text": text})
    ).json()

    assert [(e["path"], e["line"]) for e in body["errors"]] == [("rows[0].widgets[0].query", 11)]


# ── Who may do what ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "method", "path", "body", "code"),
    [
        ("reader", "post", "", {"spec": SPEC}, 403),
        ("reader", "patch", "/sales-overview", {"base_version": 2, "spec": SPEC}, 403),
        ("reader", "delete", "/sales-overview/draft", None, 403),
        ("reader", "get", "/sales-overview?draft=true", None, 403),
        ("reader", "get", "/sales-overview/source?draft=true", None, 403),
        ("reader", "get", "/sales-overview", None, 200),
        ("reader", "get", "/sales-overview/source", None, 200),
        ("reader", "get", "/sales-overview/revisions", None, 200),
        ("writer", "patch", "/sales-overview", {"base_version": 2, "spec": SPEC}, 200),
        ("writer", "get", "/sales-overview?draft=true", None, 200),
        ("writer", "post", "/sales-overview/publish", {"base_version": 2}, 403),
        ("writer", "delete", "/sales-overview", None, 403),
    ],
)
async def test_roles(client, as_owner, ws, db_session, role, method, path, body, code):
    await _create(as_owner, ws)
    await _publish(as_owner, ws, "sales-overview", 1)
    await _member(db_session, ws, role)
    await _sign_in(client, role)

    kwargs = {"json": body} if body is not None else {}
    resp = await getattr(client, method)(f"/workspaces/{ws}/dashboards{path}", **kwargs)

    assert resp.status_code == code, resp.text


@pytest.mark.parametrize("role", ["reader", "writer"])
async def test_validating_needs_the_writer_role(client, as_owner, ws, db_session, role):
    await _member(db_session, ws, role)
    await _sign_in(client, role)

    resp = await client.post(f"/workspaces/{ws}/dashboard-validations", json={"spec": spec()})

    assert resp.status_code == (403 if role == "reader" else 200)


async def test_a_stranger_sees_nothing(client, as_owner, ws, db_session):
    await _create(as_owner, ws)
    stranger = User(email="s@d.local", password_hash=hash_password("pw"), name="S", role="user")
    db_session.add(stranger)
    await db_session.commit()
    client.cookies.clear()
    await client.post("/auth/login", json={"email": "s@d.local", "password": "pw"})

    assert (await client.get(f"/workspaces/{ws}/dashboards")).status_code == 403


# ── Git-managed dashboards ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("patch", "", {"base_version": 2, "spec": SPEC}),
        ("post", "/publish", {"base_version": 2}),
        ("delete", "/draft", None),
        ("delete", "", None),
    ],
)
async def test_a_dashboard_managed_in_git_is_changed_only_from_git(
    as_owner, ws, db_session, method, path, body
):
    await _create(as_owner, ws)
    await _publish(as_owner, ws, "sales-overview", 1)
    await db_session.execute(sa.update(Dashboard).values(source="git", source_id="acme/dashboards"))
    await db_session.commit()

    kwargs = {"json": body} if body is not None else {}
    resp = await getattr(as_owner, method)(
        f"/workspaces/{ws}/dashboards/sales-overview{path}", **kwargs
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == "managed_dashboard"
    assert "acme/dashboards" in resp.json()["message"]
    read = (await as_owner.get(f"/workspaces/{ws}/dashboards/sales-overview")).json()
    assert read["managed"] is True


# ── How a change arrived ──────────────────────────────────────────────────────


async def test_a_change_records_the_client_that_made_it(as_owner, ws):
    """The web app signs in with a cookie; the CLI and other clients send a token."""
    ui = await _create(as_owner, ws, spec(name="from-the-web"))
    token = (await as_owner.post("/me/pats", json={})).json()["token"]
    as_owner.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}

    cli = await as_owner.post(
        f"/workspaces/{ws}/dashboards",
        json={"spec": spec()},
        headers={**headers, "User-Agent": "duckhaven-cli/1.4.0 (linux)"},
    )
    other = await as_owner.patch(
        f"/workspaces/{ws}/dashboards/sales-overview",
        json={"base_version": 1, "spec": spec()},
        headers={**headers, "User-Agent": "acme-sync/2.0"},
    )

    assert ui["updated_via"] == "ui"
    assert (cli.json()["updated_via"], cli.json()["client"]) == ("cli", "duckhaven-cli/1.4.0")
    assert (other.json()["updated_via"], other.json()["client"]) == ("rest", "acme-sync/2.0")
