"""Drafts saved over MCP: what they say they came from, and the limits on them.

An agent connected over MCP saves drafts as the person it acts for, through the
same routes as everyone else. The routes recognise its saves by the MCP
server's User-Agent and limit them, so a runaway agent can fill neither the
workspace's dashboard list nor its own hourly allowance without stopping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from api.config import settings
from api.models.dashboard import DashboardDraftSave

from . import test_dashboards as dashboards
from .test_dashboards import spec

# The dashboard tests' signed-in owner, and their workspace with a `sales` model.
owner = dashboards.owner
as_owner = dashboards.as_owner
ws = dashboards.ws

#: What the MCP server's loopback sends besides the person's token.
MCP = {"User-Agent": "duckhaven-mcp/1.2.0", "X-DuckHaven-MCP-Client": "claude-code/2.1.0"}


@pytest.fixture
async def token(as_owner: AsyncClient, ws: str) -> str:
    """The owner's token. The cookie goes, so each request says how it arrives."""
    token = (await as_owner.post("/me/pats", json={})).json()["token"]
    as_owner.cookies.clear()
    return token


@pytest.fixture
def agent(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **MCP}


async def _create(client: AsyncClient, ws: str, name: str, headers: dict[str, str]):
    return await client.post(
        f"/workspaces/{ws}/dashboards", json={"spec": spec(name=name)}, headers=headers
    )


async def _save(client: AsyncClient, ws: str, name: str, version: int, headers: dict[str, str]):
    return await client.patch(
        f"/workspaces/{ws}/dashboards/{name}",
        json={"base_version": version, "spec": spec(name=name, title="Again")},
        headers=headers,
    )


async def _saves(db_session) -> list[DashboardDraftSave]:
    return list((await db_session.execute(sa.select(DashboardDraftSave))).scalars())


async def test_a_draft_saved_over_mcp_names_the_agents_client(as_owner, ws, agent):
    made = await _create(as_owner, ws, "from-an-agent", agent)

    assert made.status_code == 201, made.text
    assert (made.json()["updated_via"], made.json()["client"]) == ("mcp", "claude-code/2.1.0")
    # As whom: the person whose token the agent holds.
    read = await as_owner.get(
        f"/workspaces/{ws}/dashboards/from-an-agent?draft=true", headers=agent
    )
    assert read.json()["updated_by_name"] == made.json()["updated_by_name"] == "Owner"


async def test_without_the_agents_name_a_draft_names_the_mcp_server(as_owner, ws, agent):
    unnamed = {k: v for k, v in agent.items() if k != "X-DuckHaven-MCP-Client"}

    made = await _create(as_owner, ws, "from-an-agent", unnamed)

    assert (made.json()["updated_via"], made.json()["client"]) == ("mcp", "duckhaven-mcp/1.2.0")


async def test_another_client_cannot_claim_to_be_an_agent(as_owner, ws, token):
    """The header counts only from the MCP server, which names itself in its User-Agent."""
    made = await _create(
        as_owner,
        ws,
        "from-a-script",
        {"Authorization": f"Bearer {token}", "X-DuckHaven-MCP-Client": "claude-code/2.1.0"},
    )

    assert made.json()["updated_via"] == "rest"
    assert made.json()["client"].startswith("python-httpx/")


async def test_saves_past_the_hourly_limit_are_refused(as_owner, ws, agent, monkeypatch):
    monkeypatch.setattr(settings, "mcp_dashboard_draft_saves_per_hour", 2)
    first = await _create(as_owner, ws, "first", agent)
    assert (await _save(as_owner, ws, "first", first.json()["version"], agent)).status_code == 200

    refused = await _create(as_owner, ws, "second", agent)

    assert refused.status_code == 429, refused.text
    body = refused.json()
    assert body["error"] == "draft_quota_exceeded"
    assert body["details"] == {"quota": "saves_per_hour", "limit": 2}
    assert "Try again in 60 minutes" in body["message"]
    listed = (await as_owner.get(f"/workspaces/{ws}/dashboards", headers=agent)).json()
    assert [d["name"] for d in listed["items"]] == ["first"]


async def test_saves_older_than_an_hour_do_not_count_and_are_forgotten(
    as_owner, ws, agent, db_session, monkeypatch
):
    made = await _create(as_owner, ws, "first", agent)
    await db_session.execute(
        sa.update(DashboardDraftSave).values(saved_at=datetime.now(UTC) - timedelta(hours=2))
    )
    await db_session.commit()
    monkeypatch.setattr(settings, "mcp_dashboard_draft_saves_per_hour", 1)

    saved = await _save(as_owner, ws, "first", made.json()["version"], agent)

    assert saved.status_code == 200, saved.text
    db_session.expire_all()
    (left,) = await _saves(db_session)
    assert left.saved_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)


async def test_drafts_past_the_per_person_limit_are_refused(as_owner, ws, agent, monkeypatch):
    monkeypatch.setattr(settings, "mcp_dashboard_drafts_per_user", 2)
    first = await _create(as_owner, ws, "first", agent)
    await _create(as_owner, ws, "second", agent)

    refused = await _create(as_owner, ws, "third", agent)

    assert refused.status_code == 429, refused.text
    assert refused.json()["details"] == {"quota": "drafts", "limit": 2}
    assert "Publish or discard some of them first" in refused.json()["message"]
    # Saving one of them again is not another draft.
    again = await _save(as_owner, ws, "first", first.json()["version"], agent)
    assert again.status_code == 200, again.text


async def test_a_published_draft_no_longer_counts(as_owner, ws, agent, token, monkeypatch):
    monkeypatch.setattr(settings, "mcp_dashboard_drafts_per_user", 1)
    first = await _create(as_owner, ws, "first", agent)
    published = await as_owner.post(
        f"/workspaces/{ws}/dashboards/first/publish",
        json={"base_version": first.json()["version"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert published.status_code == 200, published.text

    made = await _create(as_owner, ws, "second", agent)

    assert made.status_code == 201, made.text


async def test_saves_from_anywhere_else_are_not_limited_or_counted(
    as_owner, ws, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_dashboard_draft_saves_per_hour", 0)
    monkeypatch.setattr(settings, "mcp_dashboard_drafts_per_user", 0)

    from_the_web = await _create(as_owner, ws, "from-the-web", {})
    token = (await as_owner.post("/me/pats", json={})).json()["token"]
    as_owner.cookies.clear()
    from_a_script = await _create(
        as_owner, ws, "from-a-script", {"Authorization": f"Bearer {token}"}
    )

    assert (from_the_web.status_code, from_a_script.status_code) == (201, 201)
    assert await _saves(db_session) == []
