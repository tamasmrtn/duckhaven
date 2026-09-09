"""DuckHaven's Model Context Protocol server.

Lets an external MCP client — Claude Code, Claude Desktop, Cursor — browse
catalog metadata, run governed SQL and use the semantic layer, as an ordinary
DuckHaven principal.

The governance property is the same one
:mod:`api.services.assistant.gateway` describes, and it is load-bearing here for
the same reason: every tool call is an authenticated HTTP call to DuckHaven's own
REST API, so ``assert_workspace_member`` -> ``sql_guard`` -> ``assert_query_access``
stay server-side in the existing chokepoints. Nothing in this package touches the
control-plane database, DuckDB or Polaris.

Where it differs from the assistant is identity. The assistant acts as one fixed
service account; here the caller presents their *own* personal access token and it
is forwarded verbatim on the loopback, so tool calls run with that principal's
memberships, grants and query-history attribution. The token is scoped to exactly
the REST API it is forwarded to — same issuer, same audience — so this is not the
cross-audience token passthrough the MCP authorization spec warns about. That also
means this server is not a new privilege boundary: it can reach nothing the caller
could not already reach with ``curl`` against ``/api``.
"""

from api.services.mcp.server import mcp_asgi_app, mcp_session_manager

__all__ = ["mcp_asgi_app", "mcp_session_manager"]
