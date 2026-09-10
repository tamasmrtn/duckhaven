"""The MCP endpoint's front door: who gets in, and what a refusal looks like.

These go through the real middleware and the real in-process loopback, so a token
is accepted here only if ``GET /me`` accepts it — which is the property the whole
design rests on. Nothing here stubs the PAT lookup.
"""

import pytest
from httpx import AsyncClient

from api.config import settings
from api.services.mcp.server import MCP_PATH

from .conftest import rpc_body, rpc_headers

LIST_TOOLS = (rpc_headers("tools/list"), rpc_body("tools/list"))


async def _tools_list(mcp: AsyncClient, **kwargs):
    headers, body = LIST_TOOLS
    return await mcp.post(MCP_PATH, headers={**headers, **kwargs.pop("extra", {})}, json=body)


async def test_a_valid_token_reaches_the_server(mcp_client: AsyncClient, auth):
    resp = await _tools_list(mcp_client, extra=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["tools"]


async def test_no_credential_is_challenged(mcp_client: AsyncClient):
    resp = await _tools_list(mcp_client)
    assert resp.status_code == 401
    assert "dh_pat_" in resp.headers["WWW-Authenticate"]


async def test_the_challenge_does_not_advertise_an_oauth_server(mcp_client: AsyncClient):
    """DuckHaven has no authorization server, so the 401 must not name one.

    The MCP spec's discovery flow starts from a ``resource_metadata`` parameter on
    this header. Emitting one would send a conformant client off to fetch protected
    resource metadata and then an authorization server that does not exist — a
    dead end that reads as a broken server rather than as "bring a token".
    """
    resp = await _tools_list(mcp_client)
    challenge = resp.headers["WWW-Authenticate"]
    assert "resource_metadata" not in challenge
    assert challenge.startswith("Bearer ")
    assert 'realm="DuckHaven"' in challenge


async def test_a_garbage_token_is_refused(mcp_client: AsyncClient):
    resp = await _tools_list(mcp_client, extra={"Authorization": "Bearer dh_pat_nonsense"})
    assert resp.status_code == 401


async def test_a_revoked_token_stops_working(mcp_client: AsyncClient, client: AsyncClient, auth):
    """Revocation has to bite here too, or the MCP endpoint outlives the kill switch."""
    assert (await _tools_list(mcp_client, extra=auth)).status_code == 200

    listing = await client.get("/me/pats")
    assert listing.status_code == 200
    pat_id = listing.json()[0]["id"]
    assert (await client.delete(f"/me/pats/{pat_id}")).status_code in (200, 204)

    assert (await _tools_list(mcp_client, extra=auth)).status_code == 401


async def test_a_deactivated_principal_stops_working(
    mcp_client: AsyncClient, db_session, member, auth
):
    """Deactivating the account is the fastest response to a compromised token.

    It has to reach every front door at once, so it is asserted here rather than
    assumed from the REST API's own coverage.
    """
    assert (await _tools_list(mcp_client, extra=auth)).status_code == 200

    member.is_active = False
    db_session.add(member)
    await db_session.commit()

    assert (await _tools_list(mcp_client, extra=auth)).status_code == 401


async def test_disabling_the_feature_closes_the_endpoint(
    mcp_client: AsyncClient, auth, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_enabled", False)
    resp = await _tools_list(mcp_client, extra=auth)
    assert resp.status_code == 503
    assert "not enabled" in resp.json()["error"]["message"]


# --- Origin validation (DNS rebinding) ---------------------------------------


async def test_a_request_without_an_origin_is_allowed(mcp_client: AsyncClient, auth):
    """Absent is not invalid — no non-browser MCP client sends an Origin at all."""
    resp = await _tools_list(mcp_client, extra=auth)
    assert resp.status_code == 200


async def test_a_foreign_origin_is_forbidden(mcp_client: AsyncClient, auth):
    resp = await _tools_list(mcp_client, extra={**auth, "Origin": "http://evil.example"})
    assert resp.status_code == 403


async def test_an_allowed_origin_is_not_blocked(mcp_client: AsyncClient, auth, monkeypatch):
    """The allowlist narrows who is refused; it does not make browsers work.

    A browser-based client would still fail on the unauthenticated preflight and
    the missing CORS response headers, so this asserts only what the check itself
    promises — that an allowed origin is not the thing standing in the way.
    """
    monkeypatch.setattr(settings, "cors_origins", ["http://app.example"])
    resp = await _tools_list(mcp_client, extra={**auth, "Origin": "http://app.example"})
    assert resp.status_code == 200


async def test_a_wildcard_allowlist_permits_any_origin(mcp_client: AsyncClient, auth, monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["*"])
    resp = await _tools_list(mcp_client, extra={**auth, "Origin": "http://anything.example"})
    assert resp.status_code == 200


async def test_origin_is_checked_before_the_token(mcp_client: AsyncClient):
    """A rebinding attempt is refused as one, whether or not it carried a token."""
    resp = await _tools_list(mcp_client, extra={"Origin": "http://evil.example"})
    assert resp.status_code == 403


# --- transport shape ----------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_get_and_delete_are_rejected(mcp_client: AsyncClient, auth, method):
    """Protocol revision 2026-07-28 removed the GET stream and session termination.

    A client speaking it announces the version on every request, and gets 405 for
    those verbs rather than a session that does not exist. (Omit the version
    header and the SDK routes the request to its backward-compatibility path
    instead, where a GET still opens a long-lived stream — which is the point of
    that path, and why this asserts the modern shape explicitly.)
    """
    headers = {**rpc_headers(f"{method.lower()}-probe"), **auth}
    resp = await mcp_client.request(method, MCP_PATH, headers=headers)
    assert resp.status_code == 405


async def test_the_endpoint_answers_on_the_documented_path(mcp_client: AsyncClient, auth):
    """No trailing slash, no redirect.

    A mount would only match below /mcp and bounce the documented URL to /mcp/
    with a 307 — which not every client follows on a POST, and which would make
    every copy-pasted config in the docs wrong.
    """
    resp = await _tools_list(mcp_client, extra=auth)
    assert resp.status_code == 200


async def test_the_endpoint_also_answers_with_a_trailing_slash(mcp_client: AsyncClient, auth):
    """A misconfigured URL should still work, not look like a broken server."""
    headers, body = LIST_TOOLS
    resp = await mcp_client.post(f"{MCP_PATH}/", headers={**headers, **auth}, json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["tools"]
