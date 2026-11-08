"""Network addresses the API acts on: where a connection came from, and where an
agent's result files are fetched from."""

from __future__ import annotations

import re
import uuid

from starlette.requests import HTTPConnection

from api.config import settings
from api.models.agent import Agent

# A bare hostname or IPv4 address, or a bracketed IPv6 literal. Nothing that could
# carry a port, path, query, fragment or userinfo into a URL built around it.
_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")


def clean_host(value: object) -> str | None:
    """``value`` as a bare host, or None when it is anything else."""
    if not isinstance(value, str):
        return None
    host = value.strip()
    return host if _HOST_RE.fullmatch(host) else None


def clean_port(value: object) -> int | None:
    """``value`` as a TCP port, or None when it is not one."""
    if isinstance(value, bool):
        return None
    try:
        port = int(value)
    except TypeError, ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def peer_address(conn: HTTPConnection) -> str | None:
    """The address a request or WebSocket came from.

    With ``trusted_proxy_ingress`` the right-most ``X-Forwarded-For`` hop: the one
    the proxy in front of the API appended, which a client cannot choose. Otherwise
    the socket peer, since without a proxy the header is whatever the client sent.
    """
    if settings.trusted_proxy_ingress:
        forwarded_for = conn.headers.get("x-forwarded-for")
        if forwarded_for:
            hop = clean_host(forwarded_for.rsplit(",", 1)[-1])
            if hop:
                return hop
    return conn.client.host if conn.client else None


def result_url(agent: Agent, query_id: uuid.UUID) -> str | None:
    """The URL of ``query_id``'s result file on ``agent``, or None when the agent has
    no usable address — including a stored host that is not a bare host."""
    host = clean_host(agent.result_host)
    port = clean_port(agent.result_port)
    if host is None or port is None:
        return None
    return f"http://{host}:{port}/results/{query_id}.parquet"
