import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from api.models.query import Query
from api.models.user import Credential, User
from api.services.auth import hash_password, hash_token

from ..conftest import seed_workspace

SA_BASE = "/admin/service-accounts"


@pytest.fixture
async def admin_user(db_session) -> User:
    user = User(
        email="admin@test.local",
        password_hash=hash_password("secret"),
        name="Admin",
        role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _login(client: AsyncClient, email: str, password: str) -> None:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200


async def _as_admin(client: AsyncClient) -> None:
    await _login(client, "admin@test.local", "secret")


# --- permission gating --------------------------------------------------------


async def test_list_requires_auth(client: AsyncClient):
    resp = await client.get(SA_BASE)
    assert resp.status_code == 401


async def test_list_forbidden_for_non_admin(client: AsyncClient, db_session):
    db_session.add(
        User(
            email="user@test.local",
            password_hash=hash_password("secret"),
            name="User",
            role="user",
        )
    )
    await db_session.commit()
    await _login(client, "user@test.local", "secret")
    resp = await client.get(SA_BASE)
    assert resp.status_code == 403


# --- CRUD ---------------------------------------------------------------------


async def test_create_and_list(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    resp = await client.post(SA_BASE, json={"name": "CI Runner"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "user"  # never defaults to admin
    assert body["email"] == "ci-runner@service-account.local"
    assert body["pat_count"] == 0

    listing = await client.get(SA_BASE)
    assert listing.status_code == 200
    assert [a["name"] for a in listing.json()["items"]] == ["CI Runner"]


async def test_create_duplicate_name_conflicts(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    assert (await client.post(SA_BASE, json={"name": "dup"})).status_code == 201
    resp = await client.post(SA_BASE, json={"name": "dup"})
    assert resp.status_code == 409


async def test_patch_disable_and_role(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    resp = await client.patch(f"{SA_BASE}/{sa_id}", json={"is_active": False, "role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert resp.json()["role"] == "admin"


async def test_patch_unknown_id_404(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    resp = await client.patch(f"{SA_BASE}/{uuid.uuid4()}", json={"role": "user"})
    assert resp.status_code == 404


async def test_sa_routes_reject_human_user_id(client: AsyncClient, admin_user: User):
    # A human user's id must not be addressable through the service-account API.
    await _as_admin(client)
    resp = await client.get(f"{SA_BASE}/{admin_user.id}/pats")
    assert resp.status_code == 404


async def test_delete_without_history(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "throwaway"})).json()["id"]
    assert (await client.delete(f"{SA_BASE}/{sa_id}")).status_code == 204
    assert (await client.get(SA_BASE)).json()["items"] == []


async def test_delete_with_history_conflicts(client: AsyncClient, admin_user: User, db_session):
    await _as_admin(client)
    sa_id = uuid.UUID((await client.post(SA_BASE, json={"name": "used"})).json()["id"])
    ws, _ = await seed_workspace(db_session, user_id=admin_user.id, slug="hist-ws")
    db_session.add(Query(workspace_id=ws.id, user_id=sa_id, sql="select 1", status="succeeded"))
    await db_session.commit()
    resp = await client.delete(f"{SA_BASE}/{sa_id}")
    assert resp.status_code == 409


# --- PAT lifecycle ------------------------------------------------------------


async def test_issue_pat_returns_secret_once(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    resp = await client.post(f"{SA_BASE}/{sa_id}/pats", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["token"].startswith("dh_pat_")
    assert body["expires_at"] is not None  # 90-day default

    pats = await client.get(f"{SA_BASE}/{sa_id}/pats")
    assert pats.status_code == 200
    meta = pats.json()
    assert len(meta) == 1
    assert "token" not in meta[0]  # never re-exposed
    assert "token_hash" not in meta[0]


async def test_issue_pat_never_expires(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    resp = await client.post(f"{SA_BASE}/{sa_id}/pats", json={"expires_in_days": None})
    assert resp.json()["expires_at"] is None


async def test_bearer_resolves_service_account(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa = (await client.post(SA_BASE, json={"name": "svc"})).json()
    token = (await client.post(f"{SA_BASE}/{sa['id']}/pats", json={})).json()["token"]

    # A fresh client with no cookie, authenticating purely by bearer.
    me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == sa["email"]


async def test_bearer_revoked_rejected(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    issued = (await client.post(f"{SA_BASE}/{sa_id}/pats", json={})).json()
    token, pat_id = issued["token"], issued["id"]

    assert (await client.delete(f"{SA_BASE}/{sa_id}/pats/{pat_id}")).status_code == 204
    me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


async def test_bearer_disabled_account_rejected(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    token = (await client.post(f"{SA_BASE}/{sa_id}/pats", json={})).json()["token"]
    await client.patch(f"{SA_BASE}/{sa_id}", json={"is_active": False})
    me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


async def test_bearer_expired_rejected(client: AsyncClient, admin_user: User, db_session):
    await _as_admin(client)
    sa_id = uuid.UUID((await client.post(SA_BASE, json={"name": "svc"})).json()["id"])
    token = "dh_pat_expired-example"
    db_session.add(
        Credential(
            user_id=sa_id,
            kind="pat",
            token=None,
            token_hash=hash_token(token),
            expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
        )
    )
    await db_session.commit()
    me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


async def test_bearer_malformed_rejected(client: AsyncClient):
    me = await client.get("/me", headers={"Authorization": "Bearer not-a-dh-token"})
    assert me.status_code == 401


async def test_revoke_unknown_pat_404(client: AsyncClient, admin_user: User):
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    resp = await client.delete(f"{SA_BASE}/{sa_id}/pats/{uuid.uuid4()}")
    assert resp.status_code == 404


# --- RBAC parity with human users ---------------------------------------------


async def test_rbac_parity_service_account_vs_human(
    client: AsyncClient, admin_user: User, db_session
):
    """A service account and a human user with identical roles get identical
    enforcement on the same permission-gated endpoint."""
    await _as_admin(client)
    # role="user" service account, authenticating by bearer.
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    sa_token = (await client.post(f"{SA_BASE}/{sa_id}/pats", json={})).json()["token"]

    # role="user" human, authenticating by cookie.
    db_session.add(
        User(
            email="human@test.local",
            password_hash=hash_password("secret"),
            name="Human",
            role="user",
        )
    )
    await db_session.commit()

    admin_route = SA_BASE  # requires SERVICE_ACCOUNTS_MANAGE

    sa_resp = await client.get(admin_route, headers={"Authorization": f"Bearer {sa_token}"})
    assert sa_resp.status_code == 403

    await _login(client, "human@test.local", "secret")
    human_resp = await client.get(admin_route)
    assert human_resp.status_code == 403


async def test_workspace_membership_accepts_service_account(
    client: AsyncClient, admin_user: User, db_session
):
    """The existing admin workspace-membership endpoint works for a service
    account's user_id — no parallel membership machinery is needed."""
    await _as_admin(client)
    sa_id = (await client.post(SA_BASE, json={"name": "svc"})).json()["id"]
    ws, _ = await seed_workspace(db_session, user_id=admin_user.id, slug="grant-ws")
    resp = await client.put(f"/admin/users/{sa_id}/workspaces/{ws.slug}", json={"role": "reader"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "reader"
