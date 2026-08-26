"""The error envelope, exercised through real routes.

docs/reference/api-conventions.md promises one body shape for every 4xx and
5xx. The conformance test asserts the schema says so; these assert the server
actually does it, for each way an error can be raised.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.main import api_app
from api.models.user import User
from api.services.auth import hash_password
from api.services.polaris import PolarisBadRequestError, PolarisNotFoundError

from ..conftest import seed_workspace

ENVELOPE = {"error", "message", "details"}


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    u = User(
        email="admin@test.local", password_hash=hash_password("pw"), name="Admin", role="admin"
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "admin@test.local", "password": "pw"})
    return client


async def test_unauthenticated_request_uses_the_envelope(client: AsyncClient):
    resp = await client.get("/workspaces")
    assert resp.status_code == 401
    body = resp.json()
    assert body.keys() == ENVELOPE
    assert body["error"] == "unauthorized"


async def test_missing_resource_uses_the_envelope(admin_client: AsyncClient):
    resp = await admin_client.get("/workspaces/no-such-workspace")
    assert resp.status_code == 404
    body = resp.json()
    assert body.keys() == ENVELOPE
    assert body["error"] == "not_found"


async def test_string_detail_becomes_the_message(admin_client: AsyncClient):
    """A handler raising HTTPException(detail="...") keeps its wording."""
    await admin_client.post("/workspaces", json={"slug": "taken", "name": "Taken"})
    resp = await admin_client.post("/workspaces", json={"slug": "taken", "name": "Again"})
    assert resp.status_code == 409
    body = resp.json()
    assert body == {"error": "conflict", "message": "Slug already taken", "details": None}


async def test_structured_detail_keeps_its_machine_code(
    admin_client: AsyncClient, db_session: AsyncSession, admin: User
):
    """The query router raises detail={"error": ..., "detail": ...}; that specific
    code is the whole point of the envelope and must survive normalisation rather
    than being flattened to the one derived from the status."""
    workspace, _ = await seed_workspace(db_session, user_id=admin.id, slug="guard-ws")
    resp = await admin_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "DROP TABLE analytics.anything"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.keys() == ENVELOPE
    # No agent is connected in this fixture, so dispatch refuses before the SQL
    # guard runs. Either way the router's own code is what reaches the client.
    assert body["error"] in {"sql_not_allowed", "agent_required"}
    assert body["error"] != "unprocessable_content", "the derived code overwrote the router's"
    assert body["message"]


async def test_request_validation_uses_the_envelope(client: AsyncClient):
    """FastAPI's own 422 is normalised too, with the per-field problems kept."""
    resp = await client.post("/auth/login", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body.keys() == ENVELOPE
    assert body["error"] == "validation_error"
    assert body["details"]["errors"]


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [(PolarisNotFoundError, 404), (PolarisBadRequestError, 422)],
)
async def test_polaris_errors_use_the_envelope(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    admin: User,
    fake_polaris,
    exc,
    expected_status,
):
    """PolarisError has its own handler; it must produce the same shape."""
    workspace, catalog = await seed_workspace(db_session, user_id=admin.id, slug="polaris-ws")

    async def _boom(*_args, **_kwargs):
        raise exc("upstream said no")

    fake_polaris.list_schemas = _boom
    resp = await admin_client.get(f"/workspaces/{workspace.slug}/catalogs/{catalog.slug}/schemas")
    assert resp.status_code == expected_status
    body = resp.json()
    assert body.keys() == ENVELOPE
    assert "upstream said no" in body["message"]


async def test_an_unhandled_exception_still_uses_the_envelope(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """A crash is exactly when a client most needs a parseable body.

    Without a handler for it, an uncaught exception leaves through Starlette as
    `text/plain` "Internal Server Error" — the one response the SPA's parser
    returns nothing for.

    Uses its own transport: the shared client re-raises app exceptions, which is
    right for every other test and wrong for the one asserting what a caller sees
    when the app breaks.
    """
    from api.routers import workspaces

    def _boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(workspaces, "lookup_workspace", _boom)
    transport = ASGITransport(app=api_app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies=admin_client.cookies
    ) as crashing:
        resp = await crashing.get("/workspaces/anything")

    assert resp.status_code == 500
    body = resp.json()
    assert body.keys() == ENVELOPE
    assert body["error"] == "internal_error"
    assert "kaboom" not in resp.text, "the traceback must not reach the caller"


async def test_an_unmapped_4xx_does_not_read_as_a_server_fault(client: AsyncClient):
    """405 has no entry in the derived-code table. It must still say the caller
    got it wrong, not that the server broke."""
    resp = await client.request("DELETE", "/version")
    assert resp.status_code == 405
    body = resp.json()
    assert body.keys() == ENVELOPE
    assert body["error"] == "bad_request"
