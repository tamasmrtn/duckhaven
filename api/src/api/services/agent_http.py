"""The one HTTP client the API uses to reach agents' result servers and peer replicas.

Building an ``httpx.AsyncClient`` builds an SSL context too, even for plain HTTP:
about 2.8 ms of CPU on the event loop each time, which was paid on every result
page, cache probe and cross-replica forward while each call made its own client.
One shared client also keeps connections to agents alive between pages.

Per-agent auth goes on each request, never on the client, and so does any timeout
that differs from the default. The connection count is not capped, as it was not
with a client per call: a cap would make one user's long result download queue
another user's page behind it.
"""

from __future__ import annotations

import asyncio

import httpx

_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=32, keepalive_expiry=5.0)

_client: httpx.AsyncClient | None = None
_loop: asyncio.AbstractEventLoop | None = None


def client() -> httpx.AsyncClient:
    """The shared client, created on first use.

    It is bound to the event loop it was created on: a connection pool cannot be
    shared across loops, so a call from a different loop (each test has its own)
    gets a fresh client instead.
    """
    global _client, _loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _loop is not loop:
        _client = httpx.AsyncClient(limits=_LIMITS)
        _loop = loop
    return _client


async def aclose() -> None:
    """Close the shared client; called once at shutdown."""
    global _client, _loop
    if _client is not None and _loop is asyncio.get_running_loop():
        await _client.aclose()
    _client = None
    _loop = None
