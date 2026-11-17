"""Who is making the current request, and from where, for the audit log.

A pure-ASGI middleware gives each HTTP request and WebSocket a mutable
:class:`AuditContext` through a ContextVar: a request id (echoed back as
``X-Request-ID``), the source address and the user agent. Authentication fills
in the actor by mutating the same object (:func:`set_actor`), which is why it is
a holder rather than a value: a dependency runs in a copy of the request's
context, so setting a new value there would be lost, while a mutation is seen.

Work outside a request (a scheduled run, a background loop) has no context, and
what it records is attributed to the system unless it names an actor.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.models.user import SERVICE_ACCOUNT_PROVIDER, User
from api.services.addresses import peer_address

REQUEST_ID_HEADER = "X-Request-ID"
_USER_AGENT_MAX = 512


@dataclass
class AuditContext:
    """One request's audit facts. Mutated in place as the request authenticates."""

    request_id: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    actor_id: uuid.UUID | None = None
    actor_email: str | None = None
    actor_type: str = "anonymous"
    auth_method: str | None = None
    credential_id: uuid.UUID | None = None
    on_behalf_of_id: uuid.UUID | None = None
    on_behalf_of_email: str | None = None
    #: The engine the request's database session uses, so an event recorded in
    #: its own transaction (a denial, whose request rolls back) lands in the same
    #: database, including a test's.
    bind: Any = None
    #: The SQL the request last had checked against grants, for a denial's record.
    statement: str | None = None


_current: ContextVar[AuditContext | None] = ContextVar("duckhaven_audit_context", default=None)


def current() -> AuditContext | None:
    """The current request's context, or None outside a request."""
    return _current.get()


def actor_type_of(user: User) -> str:
    return "service_account" if user.auth_provider == SERVICE_ACCOUNT_PROVIDER else "user"


def set_actor(
    user: User,
    *,
    auth_method: str,
    credential_id: uuid.UUID | None = None,
    on_behalf_of: User | None = None,
    bind: Any = None,
) -> None:
    """Record who authenticated the current request. A no-op outside a request."""
    ctx = current()
    if ctx is None:
        return
    ctx.actor_id = user.id
    ctx.actor_email = user.email
    ctx.actor_type = actor_type_of(user)
    ctx.auth_method = auth_method
    ctx.credential_id = credential_id
    ctx.on_behalf_of_id = on_behalf_of.id if on_behalf_of is not None else None
    ctx.on_behalf_of_email = on_behalf_of.email if on_behalf_of is not None else None
    if bind is not None:
        ctx.bind = bind


def note_statement(sql: str) -> None:
    """Keep the SQL being checked, so a denial of it can say what was denied."""
    ctx = current()
    if ctx is not None:
        ctx.statement = sql


def origin_client() -> tuple[str, int]:
    """The ``(host, port)`` an in-process loopback request should claim to come from.

    The assistant and the MCP server call the API in process; passing the
    original caller's address keeps it on what those calls record.
    """
    ctx = current()
    return (ctx.source_ip if ctx is not None and ctx.source_ip else "127.0.0.1", 0)


class AuditContextMiddleware:
    """Give every HTTP request and WebSocket its own :class:`AuditContext`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        user_agent = conn.headers.get("user-agent")
        ctx = AuditContext(
            request_id=uuid.uuid4().hex,
            source_ip=peer_address(conn),
            user_agent=user_agent[:_USER_AGENT_MAX] if user_agent else None,
        )

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = ctx.request_id or ""
            await send(message)

        token = _current.set(ctx)
        try:
            await self.app(
                scope, receive, send_with_request_id if scope["type"] == "http" else send
            )
        finally:
            _current.reset(token)
