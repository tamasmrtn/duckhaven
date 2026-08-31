from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from api.models.user import Credential, User
from api.services.auth import PAT_PREFIX, hash_password, hash_token


@pytest.fixture
async def admin_user(db_session):
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


async def test_login_ok(client: AsyncClient, admin_user: User):
    resp = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@test.local"
    assert "session" in resp.cookies


async def test_login_wrong_password(client: AsyncClient, admin_user: User):
    resp = await client.post("/auth/login", json={"email": "admin@test.local", "password": "wrong"})
    assert resp.status_code == 401


async def test_login_unknown_email(client: AsyncClient):
    resp = await client.post("/auth/login", json={"email": "nobody@test.local", "password": "x"})
    assert resp.status_code == 401


async def test_me_with_session(client: AsyncClient, admin_user: User):
    login = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert login.status_code == 200
    resp = await client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@test.local"


async def test_me_no_cookie(client: AsyncClient):
    resp = await client.get("/me")
    assert resp.status_code == 401


async def test_logout_clears_session(client: AsyncClient, admin_user: User):
    await client.post("/auth/login", json={"email": "admin@test.local", "password": "secret"})
    resp = await client.post("/auth/logout")
    assert resp.status_code == 204
    me = await client.get("/me")
    assert me.status_code == 401


async def test_login_returns_permissions(client: AsyncClient, admin_user: User):
    resp = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert resp.status_code == 200
    assert "users:manage" in resp.json()["permissions"]


async def test_methods_local_only_by_default(client: AsyncClient):
    resp = await client.get("/auth/methods")
    assert resp.status_code == 200
    data = resp.json()
    assert data["local"] is True
    assert data["ldap"] is False
    assert data["oidc_providers"] == []


async def test_inactive_user_cannot_login(client: AsyncClient, db_session, admin_user: User):
    admin_user.is_active = False
    db_session.add(admin_user)
    await db_session.commit()
    resp = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert resp.status_code == 401


async def test_deactivating_user_blocks_live_session(
    client: AsyncClient, db_session, admin_user: User
):
    await client.post("/auth/login", json={"email": "admin@test.local", "password": "secret"})
    assert (await client.get("/me")).status_code == 200
    admin_user.is_active = False
    db_session.add(admin_user)
    await db_session.commit()
    assert (await client.get("/me")).status_code == 401


# --- Self-service PAT issuance (POST /me/pats) ------------------------------


async def _login(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/login", json={"email": "admin@test.local", "password": "secret"}
    )
    assert resp.status_code == 200


async def test_issue_own_pat_returns_a_usable_token(client: AsyncClient, admin_user: User):
    await _login(client)
    resp = await client.post("/me/pats", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["token"].startswith(PAT_PREFIX)
    assert body["expires_at"] is not None

    # The whole point: the token authenticates as the issuing user on its own.
    client.cookies.clear()
    me = await client.get("/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@test.local"


async def test_issue_own_pat_stores_only_a_hash(client: AsyncClient, db_session, admin_user: User):
    await _login(client)
    token = (await client.post("/me/pats", json={})).json()["token"]

    cred = (
        await db_session.execute(
            select(Credential).where(Credential.user_id == admin_user.id, Credential.kind == "pat")
        )
    ).scalar_one()
    assert cred.token is None
    assert cred.token_hash == hash_token(token)


async def test_issue_own_pat_honours_expires_in_days(client: AsyncClient, admin_user: User):
    await _login(client)
    resp = await client.post("/me/pats", json={"expires_in_days": 7})
    assert resp.status_code == 201
    expires_at = datetime.fromisoformat(resp.json()["expires_at"])
    assert timedelta(days=6) < expires_at - datetime.now(tz=UTC) <= timedelta(days=7)


@pytest.mark.parametrize("days", [0, -1, 366])
async def test_issue_own_pat_rejects_an_unbounded_expiry(
    client: AsyncClient, admin_user: User, days: int
):
    """Self-service tokens are always bounded, unlike the admin-issued form."""
    await _login(client)
    resp = await client.post("/me/pats", json={"expires_in_days": days})
    assert resp.status_code == 422


async def test_a_pat_cannot_mint_another_pat(client: AsyncClient, admin_user: User):
    """The reason this route is cookie-only: a token that mints tokens would
    outlive its own revocation."""
    await _login(client)
    token = (await client.post("/me/pats", json={})).json()["token"]

    client.cookies.clear()
    resp = await client.post("/me/pats", json={}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "session_required"


async def test_issue_own_pat_requires_authentication(client: AsyncClient):
    resp = await client.post("/me/pats", json={})
    assert resp.status_code == 401


async def test_issue_own_pat_declares_cookie_auth_only(client: AsyncClient):
    """A generated client must not offer a credential the route rejects."""
    spec = (await client.get("/openapi.json")).json()
    operation = spec["paths"]["/me/pats"]["post"]
    assert operation["security"] == [{"cookieAuth": []}]
    assert operation["operationId"] == "issue_own_pat"


# --- Listing and revoking your own tokens ----------------------------------


async def _issue(client: AsyncClient) -> str:
    resp = await client.post("/me/pats", json={})
    assert resp.status_code == 201
    return resp.json()["token"]


async def test_own_pats_lists_metadata_only(client: AsyncClient, admin_user: User):
    """A token is shown once, at creation. Only its hash is stored, so a listing
    could not return it even if it wanted to -- the same contract GitHub and
    GitLab publish."""
    await _login(client)
    token = await _issue(client)

    resp = await client.get("/me/pats")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert set(rows[0]) == {"id", "created_at", "expires_at", "current"}
    assert token not in resp.text
    assert hash_token(token) not in resp.text


async def test_the_token_making_the_request_is_marked_current(
    client: AsyncClient, admin_user: User
):
    """Three hashes look alike; without this a caller cannot tell whose expiry
    is about to break them."""
    await _login(client)
    first = await _issue(client)
    await _issue(client)
    client.cookies.clear()

    rows = (await client.get("/me/pats", headers={"Authorization": f"Bearer {first}"})).json()
    assert [row["current"] for row in rows] == [True, False]


async def test_a_cookie_caller_marks_no_token_current(client: AsyncClient, admin_user: User):
    await _login(client)
    await _issue(client)
    rows = (await client.get("/me/pats")).json()
    assert [row["current"] for row in rows] == [False]


async def test_own_pats_never_shows_another_users_tokens(
    client: AsyncClient, db_session, admin_user: User
):
    other = User(email="other@test.local", password_hash=hash_password("x"), name="Other")
    db_session.add(other)
    await db_session.commit()
    db_session.add(Credential(user_id=other.id, kind="pat", token_hash=hash_token("dh_pat_theirs")))
    await db_session.commit()

    await _login(client)
    assert (await client.get("/me/pats")).json() == []


async def test_a_bearer_token_may_list_its_own(client: AsyncClient, admin_user: User):
    """Unlike issuing: `dh auth status` runs on the stored token and must be able
    to warn about that token's expiry."""
    await _login(client)
    token = await _issue(client)
    client.cookies.clear()
    resp = await client.get("/me/pats", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


async def test_a_token_can_revoke_itself(client: AsyncClient, admin_user: User):
    """Revoking only ever removes access, so a bearer caller cannot escalate with
    it -- and a token able to retire itself is worth more than the nuisance of
    one retiring its siblings."""
    await _login(client)
    token = await _issue(client)
    pat_id = (await client.get("/me/pats")).json()[0]["id"]
    client.cookies.clear()

    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.delete(f"/me/pats/{pat_id}", headers=headers)).status_code == 204
    # It stops authenticating immediately.
    assert (await client.get("/me", headers=headers)).status_code == 401


async def test_revoking_leaves_the_other_tokens_alone(client: AsyncClient, admin_user: User):
    await _login(client)
    await _issue(client)
    keep = await _issue(client)
    first = (await client.get("/me/pats")).json()[0]["id"]

    assert (await client.delete(f"/me/pats/{first}")).status_code == 204
    client.cookies.clear()
    assert (await client.get("/me", headers={"Authorization": f"Bearer {keep}"})).status_code == 200


async def test_revoking_someone_elses_token_is_a_404_not_a_403(
    client: AsyncClient, db_session, admin_user: User
):
    """A 403 would confirm the token exists."""
    other = User(email="other2@test.local", password_hash=hash_password("x"), name="Other")
    db_session.add(other)
    await db_session.commit()
    theirs = Credential(user_id=other.id, kind="pat", token_hash=hash_token("dh_pat_theirs2"))
    db_session.add(theirs)
    await db_session.commit()
    await db_session.refresh(theirs)

    await _login(client)
    assert (await client.delete(f"/me/pats/{theirs.id}")).status_code == 404
    # And it still works, so the refusal was real.
    assert await db_session.get(Credential, theirs.id) is not None


async def test_revoking_an_unknown_token_is_a_404(client: AsyncClient, admin_user: User):
    await _login(client)
    resp = await client.delete("/me/pats/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 404


async def test_listing_own_pats_requires_authentication(client: AsyncClient):
    assert (await client.get("/me/pats")).status_code == 401
