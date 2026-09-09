"""Authenticate an MCP caller, and open the governed loopback it will use.

The MCP server never resolves a token itself. It opens an authenticated loopback
client with whatever bearer token the caller presented and asks the REST API
``GET /me`` who that is: 200 names the principal, anything else is a 401. So the
whole PAT contract -- ``kind == "pat"``, the expiry filter, ``is_active``, and the
deactivate-the-account kill switch -- is enforced in the one place that already
enforces it (:func:`api.services.auth.get_pat_user`, reached through
:func:`api.deps.get_current_user`), and this package needs no database session at
all.

The authenticated client and principal are handed downstream on the **ASGI scope**
rather than a context variable. That is not a style preference: the SDK's stateless
transport dispatches each request into the session manager's own task group, which
was started at application startup, so a ``ContextVar`` set here would not be
visible inside a tool handler. The scope travels with the request object the
transport frames every message with, so it survives that hop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from api.config import settings
from api.deps import _bearer

#: Where :class:`PatAuthMiddleware` leaves the authenticated call for the tools.
#: A private key on the ASGI scope, read back via ``ctx.request_context.request``.
SCOPE_KEY = "duckhaven.mcp_call"

#: Base URL of the in-process loopback. No socket is involved -- httpx needs a
#: URL to join relative paths against, exactly as the assistant's runner does.
LOOPBACK_BASE_URL = "http://mcp.internal"

_CHALLENGE = (
    'Bearer realm="DuckHaven", error="invalid_token", '
    'error_description="Supply a DuckHaven personal access token (dh_pat_...) as a bearer token."'
)


@dataclass(frozen=True)
class McpCall:
    """One authenticated MCP request: who is calling, and the client to call as."""

    user_id: str
    email: str
    client: httpx.AsyncClient


def _allowed_origin(origin: str) -> bool:
    """Whether a browser at ``origin`` may talk to this endpoint.

    Reuses the CORS allowlist the deployment already maintains for the SPA rather
    than adding a second, separately-configured list to keep in step.
    """
    return "*" in settings.cors_origins or origin in settings.cors_origins


async def _respond(
    send: Send, status: int, message: str, headers: dict[str, str] | None = None
) -> None:
    """Refuse the request with a JSON-RPC error response carrying no ``id``.

    The shape the Streamable HTTP binding sanctions for a transport-level refusal:
    the request never became a JSON-RPC message, so there is no id to answer.
    """
    body = json.dumps(
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": message}}
    ).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    for name, value in (headers or {}).items():
        raw.append((name.encode(), value.encode()))
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


class PatAuthMiddleware:
    """Gate the MCP endpoint on a DuckHaven access token, per request.

    Plain ASGI rather than ``BaseHTTPMiddleware`` so the downstream app is invoked
    inside this call -- the loopback client stays open for the whole request,
    including a streamed response, and closes when it ends.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not settings.mcp_enabled:
            await _respond(send, 503, "The MCP server is not enabled in this deployment.")
            return

        headers = Headers(scope=scope)

        # Spec, Streamable HTTP "Security & Endpoint": servers MUST validate the
        # Origin header to prevent DNS rebinding, and MUST answer 403 when one is
        # present and invalid. Absent is not invalid -- no non-browser client
        # sends one, and every MCP client this endpoint exists for is one of those.
        origin = headers.get("origin")
        if origin is not None and not _allowed_origin(origin):
            await _respond(send, 403, f"Origin {origin!r} is not allowed.")
            return

        token = _bearer(headers.get("authorization"))
        if token is None:
            await _respond(send, 401, "Authentication required.", {"WWW-Authenticate": _CHALLENGE})
            return

        # Imported here, not at module load: main imports this package, so a
        # module-level import would close the cycle (main -> mcp -> main).
        from api.main import api_app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app),
            base_url=LOOPBACK_BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.mcp_query_timeout_s + 30.0,
        ) as client:
            me = await client.get("/me")
            if me.status_code != 200:
                await _respond(
                    send, 401, "Invalid or expired token.", {"WWW-Authenticate": _CHALLENGE}
                )
                return
            body: dict[str, Any] = me.json()
            scope[SCOPE_KEY] = McpCall(
                user_id=str(body["id"]), email=body.get("email", ""), client=client
            )
            await self.app(scope, receive, send)


def current_call(request: Any) -> McpCall:
    """The authenticated call behind a tool invocation.

    ``request`` is ``ctx.request_context.request`` -- the Starlette request the
    transport framed this message with. Absence means the tool was reached without
    passing the middleware, which is a wiring bug rather than a caller error.
    """
    call = getattr(request, "scope", {}).get(SCOPE_KEY)
    if call is None:  # pragma: no cover - unreachable through the mounted endpoint
        raise RuntimeError("MCP tool invoked outside an authenticated request")
    return call
