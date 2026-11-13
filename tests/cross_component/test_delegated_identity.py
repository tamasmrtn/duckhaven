"""A delegated token across the real API and agent, on Postgres.

The assistant's per-turn token authenticates as its service account and acts for
one person. The unit suite proves each access check on SQLite; this drives one
such token through the live stack: the new columns and foreign keys, the run
stamped with its person on insert, and the run executing on a real agent.

The token is written straight into the database, the way the assistant's runtime
mints it, because no API issues one.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import uuid

import httpx
import pytest

pytestmark = pytest.mark.cross_component


async def _delegated_token(account_id: str, person_id: str) -> str:
    import asyncpg

    token = f"dh_pat_{secrets.token_urlsafe(32)}"
    conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    try:
        await conn.execute(
            "INSERT INTO credentials (id, user_id, on_behalf_of_id, kind, token_hash, expires_at) "
            "VALUES ($1, $2, $3, 'pat', $4, now() + interval '10 minutes')",
            uuid.uuid4(),
            uuid.UUID(account_id),
            uuid.UUID(person_id),
            hashlib.sha256(token.encode()).hexdigest(),
        )
    finally:
        await conn.close()
    return token


async def _finished(client: httpx.AsyncClient, query_id: str) -> dict:
    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        body = (await client.get(f"/api/queries/{query_id}")).json()
        if body["status"] in ("done", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"query {query_id} did not finish in time")


async def test_a_delegated_token_runs_only_where_both_belong(
    stack, api_client, workspace, healthy_agent
) -> None:
    suffix = uuid.uuid4().hex[:8]
    account = (
        await api_client.post(
            "/api/admin/service-accounts", json={"name": f"xc-assistant-{suffix}"}
        )
    ).json()
    person = (
        await api_client.post(
            "/api/admin/users",
            json={"email": f"pat-{suffix}@xc.test", "name": "Pat", "password": "xc-password-123"},
        )
    ).json()
    added = await api_client.post(
        f"/api/workspaces/{workspace}/members", json={"user_id": account["id"], "role": "reader"}
    )
    added.raise_for_status()
    token = await _delegated_token(account["id"], person["id"])
    submit = {"sql": "SELECT 1 AS n", "agent_id": healthy_agent["id"]}

    async with httpx.AsyncClient(
        base_url=stack.base_url, timeout=30.0, headers={"Authorization": f"Bearer {token}"}
    ) as delegated:
        # The account alone could run this; the person is not in the workspace.
        refused = await delegated.post(f"/api/workspaces/{workspace}/queries", json=submit)
        assert refused.status_code == 403, refused.text

        added = await api_client.post(
            f"/api/workspaces/{workspace}/members",
            json={"user_id": person["id"], "role": "reader"},
        )
        added.raise_for_status()
        created = await delegated.post(f"/api/workspaces/{workspace}/queries", json=submit)
        assert created.status_code == 202, created.text
        run = await _finished(delegated, created.json()["id"])
        assert run["status"] == "done", run
        assert (run["user_id"], run["on_behalf_of_id"]) == (account["id"], person["id"])

        history = await api_client.get(
            f"/api/workspaces/{workspace}/queries", params={"user_id": person["id"]}
        )
        assert [(q["id"], q["on_behalf_of_name"]) for q in history.json()["items"]] == [
            (run["id"], "Pat")
        ]

        # Switching the person off stops the token, whatever the account may do.
        disabled = await api_client.patch(
            f"/api/admin/users/{person['id']}", json={"is_active": False}
        )
        disabled.raise_for_status()
        assert (await delegated.get(f"/api/workspaces/{workspace}")).status_code == 401
