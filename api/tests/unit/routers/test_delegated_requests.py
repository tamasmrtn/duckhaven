"""Requests made with a delegated token: the assistant's, acting for one person.

The token authenticates as the service account and carries the person
(``Credential.on_behalf_of_id``). These drive it through the REST API the way the
assistant's loopback does, next to an ordinary token for the same account, which
must behave exactly as before.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import update

from api.models.agent import Agent
from api.models.catalog import WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.user import Credential, User
from api.models.workspace import WorkspaceMember
from api.services.agent_registry import registry
from api.services.auth import generate_pat, hash_password, hash_token

SQL = "SELECT * FROM analytics.orders"


@pytest_asyncio.fixture
async def account(db_session):
    sa = User(
        email="assistant@service-account.local",
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add(sa)
    await db_session.commit()
    await db_session.refresh(sa)
    return sa


@pytest_asyncio.fixture
async def person(db_session):
    human = User(
        email="pat@people.local", name="Pat", password_hash=hash_password("pw"), role="user"
    )
    db_session.add(human)
    await db_session.commit()
    await db_session.refresh(human)
    return human


@pytest_asyncio.fixture
async def ws(db_session, account):
    """A workspace the account is a reader of, and the person is not yet in."""
    workspace, _cat = await seed_workspace(db_session, user_id=account.id, role="reader")
    return workspace


async def _token(db_session, account, on_behalf_of=None) -> str:
    token = generate_pat()
    db_session.add(
        Credential(
            user_id=account.id,
            on_behalf_of_id=on_behalf_of.id if on_behalf_of is not None else None,
            kind="pat",
            token_hash=hash_token(token),
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )
    )
    await db_session.commit()
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_token_for_no_one_behaves_as_before(client: AsyncClient, db_session, account, ws):
    token = await _token(db_session, account)

    assert (await client.get(f"/workspaces/{ws.slug}", headers=_bearer(token))).status_code == 200
    listed = (await client.get("/workspaces", headers=_bearer(token))).json()
    assert [w["slug"] for w in listed] == [ws.slug]


async def test_a_delegated_token_reaches_only_workspaces_the_person_is_in(
    client: AsyncClient, db_session, account, person, ws
):
    token = await _token(db_session, account, on_behalf_of=person)
    headers = _bearer(token)

    assert (await client.get(f"/workspaces/{ws.slug}", headers=headers)).status_code == 403
    assert (await client.get("/workspaces", headers=headers)).json() == []

    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=person.id, role="owner"))
    await db_session.commit()
    assert (await client.get(f"/workspaces/{ws.slug}", headers=headers)).status_code == 200
    assert [w["slug"] for w in (await client.get("/workspaces", headers=headers)).json()] == [
        ws.slug
    ]


async def test_a_delegated_token_gets_the_lower_role(
    client: AsyncClient, db_session, account, person, ws
):
    """Saving a query needs a writer: the account is one, the person only a reader."""
    await db_session.execute(
        update(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == account.id)
        .values(role="writer")
    )
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=person.id, role="reader"))
    await db_session.commit()

    async def save(token, name):
        return await client.post(
            f"/workspaces/{ws.slug}/saved-queries",
            json={"name": name, "sql": "SELECT 1"},
            headers=_bearer(token),
        )

    assert (await save(await _token(db_session, account), "alone")).status_code == 201
    delegated = await _token(db_session, account, on_behalf_of=person)
    assert (await save(delegated, "delegated")).status_code == 403


async def test_a_delegated_token_has_only_the_permissions_both_hold(
    client: AsyncClient, db_session, account, person
):
    person.role = "admin"
    await db_session.commit()
    token = await _token(db_session, account, on_behalf_of=person)

    me = (await client.get("/me", headers=_bearer(token))).json()
    assert me["id"] == str(account.id)
    assert me["permissions"] == []


async def test_disabling_the_person_stops_their_token(
    client: AsyncClient, db_session, account, person, ws
):
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=person.id, role="reader"))
    await db_session.commit()
    token = await _token(db_session, account, on_behalf_of=person)
    assert (await client.get(f"/workspaces/{ws.slug}", headers=_bearer(token))).status_code == 200

    person.is_active = False
    await db_session.commit()
    assert (await client.get(f"/workspaces/{ws.slug}", headers=_bearer(token))).status_code == 401


# --- Running SQL in a scoped catalog -------------------------------------------


class _Socket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(
        name="agent",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"]},
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    registry.register(a.id, _Socket())  # type: ignore[arg-type]
    yield a
    registry.unregister(a.id)


@pytest_asyncio.fixture
async def scoped(db_session, account, person):
    """A scoped catalog both are readers of, with a grant for the account only."""
    workspace, cat = await seed_workspace(db_session, user_id=account.id, role="reader")
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == workspace.id)
        .values(access_mode="scoped")
    )
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=person.id, role="reader"))
    db_session.add(
        CatalogGrant(user_id=account.id, catalog_id=cat.id, schema_name="analytics", tier="reader")
    )
    await db_session.commit()
    return workspace, cat


async def _submit(client, workspace, agent, token):
    return await client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": SQL, "agent_id": str(agent.id)},
        headers=_bearer(token),
    )


async def test_a_query_the_person_may_not_run_is_refused(
    client: AsyncClient, db_session, account, person, scoped, agent
):
    workspace, _cat = scoped

    alone = await _submit(client, workspace, agent, await _token(db_session, account))
    assert alone.status_code == 202

    delegated = await _submit(
        client, workspace, agent, await _token(db_session, account, on_behalf_of=person)
    )
    assert delegated.status_code == 403
    assert delegated.json()["error"] == "grant_denied"


async def test_a_delegated_run_is_recorded_as_made_for_the_person(
    client: AsyncClient, db_session, account, person, scoped, agent
):
    workspace, cat = scoped
    db_session.add(
        CatalogGrant(user_id=person.id, catalog_id=cat.id, schema_name="analytics", tier="reader")
    )
    await db_session.commit()

    resp = await _submit(
        client, workspace, agent, await _token(db_session, account, on_behalf_of=person)
    )
    assert resp.status_code == 202
    run = resp.json()
    assert (run["user_id"], run["on_behalf_of_id"]) == (str(account.id), str(person.id))

    # The person's own History includes the runs made for them, and says so.
    await client.post("/auth/login", json={"email": "pat@people.local", "password": "pw"})
    mine = (
        await client.get(f"/workspaces/{workspace.slug}/queries", params={"user_id": person.id})
    ).json()["items"]
    assert [(q["id"], q["user_name"], q["on_behalf_of_name"]) for q in mine] == [
        (run["id"], "Assistant", "Pat")
    ]
