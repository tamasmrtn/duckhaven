"""Assemble the MCP server and its ASGI endpoint.

Built once at import and registered by :mod:`api.main` at ``/mcp``, which is all
the Streamable HTTP transport needs: one path that accepts POST.

Two wiring details are load-bearing and not obvious:

* The endpoint is a plain ``Route``, not a ``Mount``. Starlette's ``Mount`` only
  matches paths *below* its prefix, so ``POST /mcp`` on a mount would 307 to
  ``/mcp/`` -- a redirect some clients will not follow on a POST, and a URL nobody
  would think to configure. ``main`` registers the trailing-slash form as a second
  route so a stray character is not swallowed by the SPA catch-all;
  :class:`~api.services.mcp.auth.PatAuthMiddleware` normalises it back before the
  SDK's own single route sees it.
* ``transport_security`` is passed explicitly. Left unset, the SDK infers
  DNS-rebinding protection from its ``host`` default of ``127.0.0.1`` and rejects
  every request whose ``Host`` header is not localhost -- which is every request to
  a real deployment. Origin validation still happens, in
  :class:`~api.services.mcp.auth.PatAuthMiddleware`, against the CORS allowlist the
  deployment already maintains.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp

from api.config import settings
from api.services.assistant.knowledge.loader import docs_available
from api.services.mcp import tools
from api.services.mcp.auth import MCP_PATH, PatAuthMiddleware

#: Re-exported: ``MCP_PATH`` is the path the endpoint is served at on the outer
#: app, and the canonical URI a client configures (``https://<host>/mcp``). It is
#: defined in :mod:`api.services.mcp.auth`, which cannot import this module.
__all__ = ["MCP_PATH", "build_endpoint", "build_server", "mcp_asgi_app", "mcp_session_manager"]

INSTRUCTIONS = """\
DuckHaven is a self-hosted lakehouse: DuckDB compute over Apache Iceberg tables
governed by Apache Polaris. These tools read and query it as you, with your own
workspace membership and catalog grants — you cannot see or do more here than you
could in the web app.

Start with `list_workspaces`; every other tool needs a workspace slug.

Writing SQL for `run_sql`:

- The dialect is DuckDB reading Iceberg. Address tables as
  `catalog.schema.table`, or `schema.table` when you pass a `catalog`.
- Read a table's columns with `describe_table`, not `information_schema.columns`
  — that view does not report an Iceberg table's columns.
- Time travel is read-only: `SELECT ... FROM t AT (VERSION => <snapshot_id>)`.
- Only certain statements are allowed. The server rejects anything else before a
  compute agent sees it, and says which rule refused it; treat that as final
  rather than rephrasing the same statement.

When a question is about a business measure — revenue, orders, churn — call
`search_semantic` before browsing the catalog. A published metric is what the
organization agreed the number means; computing your own from column names
produces a second, quietly different answer.
"""

DOCS_INSTRUCTIONS = """\

For questions about DuckHaven itself — what a feature does, how to configure it,
what its limits are — use `search_docs` and `read_doc_page` rather than general
knowledge of other data platforms. These pages ship with the running deployment,
so they describe the version in front of you rather than the latest release.
"""


def build_server() -> MCPServer:
    """The MCP server for this deployment, with its tools registered."""
    read_only = list(tools.READ_TOOLS)
    instructions = INSTRUCTIONS
    # Withheld outright rather than left to fail at call time: a tool in the
    # schema is a tool the agent will reach for, and one that always answers
    # "not available here" spends a turn teaching it that. Mirrors the
    # assistant's build_toolset, and shares its switch — ASSISTANT_DOCS_ENABLED
    # is the deployment's "AI may read the shipped documentation" decision, and
    # having it mean one thing for the panel and another over MCP would be a
    # setting an operator cannot reason about. The instructions follow the
    # tools, so a deployment without them is never told to call them.
    if settings.assistant_docs_enabled and docs_available():
        read_only += tools.DOCS_TOOLS
        instructions += DOCS_INSTRUCTIONS

    server = MCPServer(
        "duckhaven",
        title="DuckHaven",
        version=settings.app_version,
        instructions=instructions,
        website_url=settings.docs_site_url,
    )
    for tool in read_only:
        server.tool(
            annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        )(tool)
    # The hint describes how this deployment is configured at boot; the refusal
    # itself is re-checked on every call, so flipping the setting takes effect
    # immediately even though the advertised annotation does not.
    server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=not settings.mcp_allow_writes,
            destructiveHint=settings.mcp_allow_writes,
            openWorldHint=False,
        ),
    )(tools.run_sql)
    return server


def build_endpoint() -> tuple[MCPServer, ASGIApp]:
    """A fresh MCP server and the authenticated ASGI endpoint serving it.

    A factory as well as the module-level instance below, because the transport's
    session manager can be run exactly once per instance -- so anything that needs
    a second live endpoint (a test, most obviously) has to build its own rather
    than re-enter this one's.
    """
    server = build_server()
    asgi = PatAuthMiddleware(
        server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            # One request, one identity: nothing is carried between calls that
            # could let a later request inherit an earlier caller's authentication.
            stateless_http=True,
            # No tool emits progress notifications, so a per-request SSE stream
            # would carry exactly one event. A plain JSON response is one less
            # thing for a reverse proxy in front of the deployment to buffer.
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
    )
    return server, asgi


_server, mcp_asgi_app = build_endpoint()


@asynccontextmanager
async def mcp_session_manager() -> AsyncIterator[None]:
    """Run the transport's session manager for the lifetime of the application.

    A mounted sub-application's lifespan never runs, so the host has to enter this
    itself -- the same reason ``api.main._outer_lifespan`` drives ``api_app``'s.
    Without it the first request fails with "Task group is not initialized".
    """
    async with _server.session_manager.run():
        yield
