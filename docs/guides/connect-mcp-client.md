# Connect an MCP client

DuckHaven exposes a [Model Context Protocol server](../concepts/mcp-server.md) at `/mcp`, so an AI agent — Claude Code,
Claude Desktop, Cursor, or anything else that speaks MCP — can browse your catalogs and run governed SQL on your
behalf. This page connects one.

Two things are needed: the endpoint URL, and an access token.

## 1. Get a token

The agent authenticates as **you**, using a DuckHaven personal access token. It will see exactly the workspaces and
tables you can see, and its queries are recorded against your name.

If you use [the command line](../getting-started/cli-quickstart.md), you already have one:

```sh
dh auth login --host https://duckhaven.example.com
dh auth tokens               # confirms which token is in play
```

Otherwise, in the web app, open your profile and issue a token, or ask an administrator for a
[service-account token](service-accounts.md) if this is for an unattended job rather than for you personally. Either
way you get a `dh_pat_…` string, shown exactly once.

!!! warning "Treat it like a password"
    The token is sent on every request the agent makes and grants everything your account can do. Put it in your
    client's secret store rather than in a file you commit, prefer a bounded expiry, and revoke it (`dh auth revoke
    <id>`, or **Manage tokens → Revoke**) the moment a laptop goes missing.

## 2. Find the endpoint

It is your DuckHaven host with `/mcp` on the end — the same host and port as the web app, with no trailing slash:

```text
https://duckhaven.example.com/mcp
```

For a local stack straight out of [the installer](../deployment/install.md), that is `http://localhost:8000/mcp`.

!!! warning "Use HTTPS outside your own machine"
    The token travels on every request. If DuckHaven is reachable over anything less trusted than a local network, put
    it behind [a reverse proxy with TLS](../deployment/reverse-proxy-tls.md) first and use the `https://` URL.

## 3. Add it to your client

### Claude Code

```sh
claude mcp add --transport http duckhaven https://duckhaven.example.com/mcp \
  --header "Authorization: Bearer dh_pat_xxxxxxxx"
```

Then `/mcp` inside Claude Code lists the server and its tools.

### Claude Desktop

**Settings → Connectors → Add custom connector**, with the URL above. Where the connector asks for a header, supply
`Authorization` with the value `Bearer dh_pat_xxxxxxxx`.

### Cursor

In `~/.cursor/mcp.json` (or a project's `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "duckhaven": {
      "url": "https://duckhaven.example.com/mcp",
      "headers": { "Authorization": "Bearer dh_pat_xxxxxxxx" }
    }
  }
}
```

### Anything else

Any client supporting MCP's Streamable HTTP transport works. Point it at the URL and have it send
`Authorization: Bearer dh_pat_xxxxxxxx` on every request. DuckHaven has no OAuth authorization server, so a client's
"sign in to this server" flow does not apply — configure the token directly.

## 4. Check it works

Ask the agent something that needs DuckHaven, and watch which tool it reaches for:

> Which DuckHaven workspaces can you see? List the tables in the first one.

It should call `list_workspaces`, then `list_catalogs` and `list_tables`. If it invents an answer instead, the server
is not connected.

To test the endpoint without an agent in the way:

```sh
curl -sS -X POST https://duckhaven.example.com/mcp \
  -H "Authorization: Bearer dh_pat_xxxxxxxx" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/list" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{
        "io.modelcontextprotocol/protocolVersion":"2026-07-28",
        "io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1"},
        "io.modelcontextprotocol/clientCapabilities":{}}}}'
```

A healthy server answers with its tool definitions — thirteen, or eleven where the deployment has
[documentation lookup](../concepts/mcp-server.md#documentation-lookup) turned off.

## Getting more out of it

- **Publish [semantic models](../concepts/semantic-layer.md).** Without them the agent works a metric out from column
  names and may quietly disagree with the number your team uses. With them it calls `query_metric`, and the filters,
  join path and date column come from the definition you agreed.
- **Name the workspace.** The agent has no ambient workspace the way a worksheet does. "In the `sales` workspace, …"
  saves it a discovery round-trip.
- **Scope the token.** For an unattended agent, prefer a service account with membership only in the workspaces it
  needs, rather than your own account's full reach.
- **Ask it about DuckHaven, not just about your data.** `search_docs` and `read_doc_page` give the agent the
  documentation for the version you are running, so "how do I read this table as it was last Tuesday?" is answered
  from DuckHaven's own time-travel syntax rather than from what the model remembers about other warehouses.

## When something is wrong

| What you see | What it means |
|---|---|
| `401` with a `WWW-Authenticate` header | No token, or one that is expired, revoked, or belongs to a deactivated account. Issue a fresh one. |
| `403` | The request carried an `Origin` header that is not in `CORS_ORIGINS`. Expected from a browser-based client; ordinary MCP clients send none. |
| `503`, "The MCP server is not enabled" | `MCP_ENABLED=false` on this deployment. See [Configuration](../reference/configuration.md#mcp-server). |
| A tool reports "Access denied" or "Not found" | Your token is working; your grants do not cover that object. Ask for [workspace membership](users-access.md#workspace-membership-and-roles) or a [grant](access-levels.md). |
| "This MCP server is read-only" | Expected. Writes are off unless an operator sets `MCP_ALLOW_WRITES=true`; see [Read-only by default](../concepts/mcp-server.md#read-only-by-default). |
| A `307` redirect, or the client cannot connect | Check the URL has no trailing slash — it is `/mcp`, not `/mcp/`. |
