import uuid

import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import select

from api.models.agent import Agent
from api.models.query import Query, SavedQuery
from api.models.user import User
from api.models.worksheet import Worksheet
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import hash_password


async def _user(db_session, email: str, name: str) -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=name, role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def user(db_session):
    return await _user(db_session, "w@sheets.local", "Writer")


@pytest_asyncio.fixture
async def other(db_session):
    return await _user(db_session, "o@sheets.local", "Other")


@pytest_asyncio.fixture
async def workspace(db_session, user: User, other: User):
    ws, _catalog = await seed_workspace(db_session, user_id=user.id)
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=other.id, role="reader"))
    await db_session.commit()
    return ws


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User, workspace: Workspace):
    await client.post("/auth/login", json={"email": "w@sheets.local", "password": "pw"})
    return client


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/auth/logout")
    await client.post("/auth/login", json={"email": email, "password": "pw"})


def _base(workspace: Workspace) -> str:
    return f"/workspaces/{workspace.slug}/worksheets"


async def _create(client: AsyncClient, workspace: Workspace, **body) -> dict:
    resp = await client.post(_base(workspace), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_defaults_and_appends_tabs(authed_client: AsyncClient, workspace: Workspace):
    first = await _create(authed_client, workspace)
    second = await _create(authed_client, workspace, title="Revenue", sql="SELECT 1")

    assert first["title"] == "Untitled"
    assert first["sql"] == ""
    assert first["is_open"] is True
    assert first["version"] == 1
    assert (first["tab_position"], second["tab_position"]) == (0, 1)
    assert second["title"] == "Revenue"


async def test_list_filters_by_status_and_title(authed_client: AsyncClient, workspace: Workspace):
    await _create(authed_client, workspace, title="Open one")
    await _create(authed_client, workspace, title="Closed one", is_open=False)

    opened = await authed_client.get(f"{_base(workspace)}?status=open")
    closed = await authed_client.get(f"{_base(workspace)}?status=closed")
    both = await authed_client.get(f"{_base(workspace)}?status=open&status=closed")
    searched = await authed_client.get(f"{_base(workspace)}?q=closed")

    assert [w["title"] for w in opened.json()["items"]] == ["Open one"]
    assert [w["title"] for w in closed.json()["items"]] == ["Closed one"]
    assert len(both.json()["items"]) == 2
    assert [w["title"] for w in searched.json()["items"]] == ["Closed one"]


async def test_list_sorts_by_position_and_pages(authed_client: AsyncClient, workspace: Workspace):
    for i in range(3):
        await _create(authed_client, workspace, title=f"Tab {i}")

    first = (await authed_client.get(f"{_base(workspace)}?sort=position&limit=2")).json()
    assert [w["title"] for w in first["items"]] == ["Tab 0", "Tab 1"]
    assert first["has_more"] is True
    rest = (
        await authed_client.get(
            f"{_base(workspace)}?sort=position&limit=2&cursor={first['cursor']}"
        )
    ).json()
    assert [w["title"] for w in rest["items"]] == ["Tab 2"]
    assert rest["has_more"] is False


async def test_worksheets_are_private_to_their_owner(
    authed_client: AsyncClient, workspace: Workspace
):
    mine = await _create(authed_client, workspace, title="Mine")

    await _login(authed_client, "o@sheets.local")
    listed = await authed_client.get(_base(workspace))
    assert listed.json()["items"] == []
    url = f"{_base(workspace)}/{mine['id']}"
    assert (await authed_client.get(url)).status_code == 404
    patched = await authed_client.patch(url, json={"is_open": False})
    assert patched.status_code == 404
    assert (await authed_client.delete(url)).status_code == 404


async def test_a_reader_keeps_their_own_worksheets(
    authed_client: AsyncClient, workspace: Workspace
):
    await _login(authed_client, "o@sheets.local")
    created = await _create(authed_client, workspace, sql="SELECT 1")
    assert created["sql"] == "SELECT 1"


async def test_non_member_is_rejected(authed_client: AsyncClient, db_session):
    stranger = await _user(db_session, "s@sheets.local", "Stranger")
    ws, _ = await seed_workspace(db_session, user_id=stranger.id, slug="other-ws")
    resp = await authed_client.get(f"/workspaces/{ws.slug}/worksheets")
    assert resp.status_code in (403, 404)


async def test_content_edit_bumps_version(authed_client: AsyncClient, workspace: Workspace):
    sheet = await _create(authed_client, workspace)
    resp = await authed_client.patch(
        f"{_base(workspace)}/{sheet['id']}", json={"sql": "SELECT 2", "base_version": 1}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sql"] == "SELECT 2"
    assert body["version"] == 2


async def test_stale_version_is_a_conflict_with_the_current_worksheet(
    authed_client: AsyncClient, workspace: Workspace
):
    sheet = await _create(authed_client, workspace)
    url = f"{_base(workspace)}/{sheet['id']}"
    await authed_client.patch(url, json={"sql": "SELECT 'window A'", "base_version": 1})

    resp = await authed_client.patch(url, json={"sql": "SELECT 'window B'", "base_version": 1})

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "worksheet_conflict"
    assert body["details"]["current"]["sql"] == "SELECT 'window A'"
    assert body["details"]["current"]["version"] == 2


async def test_content_edit_without_base_version_is_rejected(
    authed_client: AsyncClient, workspace: Workspace
):
    sheet = await _create(authed_client, workspace)
    resp = await authed_client.patch(f"{_base(workspace)}/{sheet['id']}", json={"title": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "base_version_required"


async def test_metadata_edit_leaves_version_and_updated_at(
    authed_client: AsyncClient, workspace: Workspace
):
    sheet = await _create(authed_client, workspace)
    resp = await authed_client.patch(
        f"{_base(workspace)}/{sheet['id']}", json={"catalog": "lake", "timeout_s": 60}
    )
    body = resp.json()
    assert resp.status_code == 200
    assert (body["catalog"], body["timeout_s"]) == ("lake", 60)
    assert body["version"] == 1
    assert body["updated_at"] == sheet["updated_at"]


async def test_explicit_null_clears_a_nullable_field(
    authed_client: AsyncClient, workspace: Workspace
):
    sheet = await _create(authed_client, workspace, catalog="lake")
    resp = await authed_client.patch(f"{_base(workspace)}/{sheet['id']}", json={"catalog": None})
    assert resp.json()["catalog"] is None


async def test_reopening_appends_the_tab(authed_client: AsyncClient, workspace: Workspace):
    closed = await _create(authed_client, workspace, is_open=False)
    await _create(authed_client, workspace)
    await _create(authed_client, workspace)

    resp = await authed_client.patch(f"{_base(workspace)}/{closed['id']}", json={"is_open": True})
    assert resp.json()["tab_position"] == 2


async def test_link_must_name_a_saved_query_in_the_workspace(
    authed_client: AsyncClient, workspace: Workspace, user: User, db_session
):
    other_ws, _ = await seed_workspace(db_session, user_id=user.id, slug="elsewhere")
    foreign = SavedQuery(workspace_id=other_ws.id, name="f", sql="SELECT 1", created_by=user.id)
    db_session.add(foreign)
    await db_session.commit()

    created = await authed_client.post(_base(workspace), json={"saved_query_id": str(foreign.id)})
    assert created.status_code == 422
    assert created.json()["error"] == "saved_query_not_found"


async def test_last_query_must_belong_to_the_workspace(
    authed_client: AsyncClient, workspace: Workspace, user: User, db_session
):
    sheet = await _create(authed_client, workspace)
    other_ws, _ = await seed_workspace(db_session, user_id=user.id, slug="elsewhere")
    foreign = Query(workspace_id=other_ws.id, sql="SELECT 1", status="done", user_id=user.id)
    mine = Query(workspace_id=workspace.id, sql="SELECT 1", status="done", user_id=user.id)
    db_session.add_all([foreign, mine])
    await db_session.commit()
    url = f"{_base(workspace)}/{sheet['id']}"

    rejected = await authed_client.patch(url, json={"last_query_id": str(foreign.id)})
    accepted = await authed_client.patch(url, json={"last_query_id": str(mine.id)})

    assert rejected.status_code == 422
    assert accepted.json()["last_query_id"] == str(mine.id)


async def test_an_agent_the_caller_cannot_see_is_rejected(
    authed_client: AsyncClient, workspace: Workspace, db_session
):
    hidden = Agent(name="private", status="healthy", access_mode="restricted")
    db_session.add(hidden)
    await db_session.commit()
    resp = await authed_client.post(_base(workspace), json={"agent_id": str(hidden.id)})
    assert resp.status_code == 404


async def test_delete_removes_the_worksheet(authed_client: AsyncClient, workspace: Workspace):
    sheet = await _create(authed_client, workspace)
    url = f"{_base(workspace)}/{sheet['id']}"
    assert (await authed_client.delete(url)).status_code == 204
    assert (await authed_client.get(url)).status_code == 404


async def test_deleting_a_saved_query_unlinks_its_worksheets(
    authed_client: AsyncClient, workspace: Workspace
):
    saved = (
        await authed_client.post(
            f"/workspaces/{workspace.slug}/saved-queries", json={"name": "n", "sql": "SELECT 1"}
        )
    ).json()
    sheet = await _create(authed_client, workspace, saved_query_id=saved["id"], sql="SELECT 1")

    await authed_client.delete(f"/workspaces/{workspace.slug}/saved-queries/{saved['id']}")

    after = (await authed_client.get(f"{_base(workspace)}/{sheet['id']}")).json()
    assert after["saved_query_id"] is None
    assert after["sql"] == "SELECT 1"


async def test_deleting_an_agent_clears_it_from_worksheets(
    authed_client: AsyncClient, workspace: Workspace, user: User, db_session
):
    from api.services.compute.service import delete_agent

    agent = Agent(name="gone", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    sheet = await _create(authed_client, workspace, agent_id=str(agent.id))

    await delete_agent(db_session, agent)

    after = (await authed_client.get(f"{_base(workspace)}/{sheet['id']}")).json()
    assert after["agent_id"] is None


async def test_deleting_the_workspace_removes_its_worksheets(
    authed_client: AsyncClient, workspace: Workspace, db_session
):
    from api.services.workspace import delete_workspace

    await _create(authed_client, workspace)
    ws = await db_session.get(Workspace, workspace.id)
    await delete_workspace(db_session, ws)

    left = (await db_session.execute(select(Worksheet))).scalars().all()
    assert left == []


async def test_unknown_worksheet_is_404(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.get(f"{_base(workspace)}/{uuid.uuid4()}")
    assert resp.status_code == 404
