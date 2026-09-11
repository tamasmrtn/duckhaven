# MCP server

DuckHaven speaks the [Model Context Protocol](https://modelcontextprotocol.io), so an AI agent running somewhere else —
Claude Code in your terminal, Claude Desktop, Cursor — can work with your data directly. It can list what a workspace
holds, describe a table, run SQL, and answer a question from your published
[semantic models](semantic-layer.md), all without anyone pasting data into a chat window.

It is the same idea as the built-in [AI data assistant](assistant.md), pointed outward. The assistant is a chat panel
inside DuckHaven driven by a model DuckHaven configures; the MCP server is a door in the other direction, letting an
agent you already use bring DuckHaven into whatever it is doing — reviewing a dbt model, writing a report, debugging a
pipeline.

Turning it off is a one-line setting (`MCP_ENABLED=false`); see
[Configuration](../reference/configuration.md#mcp-server). To connect a client, see
[Connect an MCP client](../guides/connect-mcp-client.md).

## It acts as you

This is the part worth understanding, because it is what makes everything else safe.

An MCP client authenticates with a DuckHaven **access token** — the same `dh_pat_…` personal access token described in
[Service accounts & tokens](../guides/service-accounts.md), issued to a person by `dh auth login` or to a service
account by an administrator. Every tool call the agent makes is then performed **as the holder of that token**, through
DuckHaven's own REST API, exactly as if that person had clicked through the web app.

So the agent sees the workspaces you are a member of and no others; reads the catalogs, schemas and tables your
[grants](permissions.md) allow and no others; and its queries appear in the query history attributed to you. The order
of checks is the one every DuckHaven client goes through:

```
workspace membership  →  SQL statement allowlist  →  scoped catalog grants  →  DuckDB execution
```

None of that enforcement lives in the MCP server. It holds no credentials of its own, and it never reads your data
except through those REST endpoints — never DuckHaven's control-plane database, never DuckDB, never
[Polaris](catalogs.md) directly. (The one thing it does read from the database is the documentation corpus, which is
public content with no grants to enforce; see [Documentation lookup](#documentation-lookup).) The practical
consequence: **the MCP server is not a new way in.** Anything an agent can
do through it, the same token could already have done with `curl` against `/api`. Anything that token cannot do, the
agent cannot do either, however the conversation goes.

That also bounds the damage from a *prompt injection* — a hostile string in a table cell that talks the agent into
running something it shouldn't. The worst outcome is something the token was already permitted to do, and it is
recorded against the token's owner.

!!! note "It differs from the assistant here"
    The assistant acts as one fixed `Assistant` service account, which an administrator grants access to centrally. The
    MCP server has no identity of its own at all — each connection carries its own. Two people connecting the same
    client to the same deployment see different data, and revoking one person's token affects only them.

## Read-only by default

Out of the box `run_sql` accepts `SELECT` and refuses `INSERT`, `UPDATE`, `DELETE` and DDL, whatever the token's grants
say — with a message explaining why, so the agent stops rather than retrying.

The reason is a gap rather than a policy preference. DuckHaven does have a way to let an AI write safely: the
assistant's [write approval](assistant.md#write-approval), where the turn pauses and a person approves or denies the
exact statement before it runs. That mechanism is a panel in the web app, and a generic MCP client has nowhere to show
it. A write over MCP would therefore be an *unattended* write, which is not what the rest of this product promises.

An operator who wants them anyway can set `MCP_ALLOW_WRITES=true`. That does not widen what any token can do — the SQL
guard and catalog grants still apply, and a token with no write grant still cannot write. It only stops DuckHaven
refusing locally what the REST API would have accepted. Set it deliberately, and prefer a token whose grants are scoped
to what the agent actually needs.

## What it can do

Fourteen tools. Twelve work with your data:

| Tool | What it does |
|---|---|
| `list_workspaces` | The workspaces this token can reach. The starting point — everything else names one. |
| `list_catalogs` | Catalogs visible in a workspace. |
| `list_schemas` | Schemas in a catalog. |
| `list_tables` | Tables in a schema. |
| `describe_table` | A table's columns, types, nullability, row count and size. |
| `get_table_lineage` | What feeds a table and what depends on it. |
| `run_sql` | Run a statement and return a capped sample of the result. |
| `get_query_result` | Page through the rest of a result you produced. |
| `search_semantic` | Find the curated metrics and dimensions a question is about. |
| `get_semantic_model` | Read one semantic model's metrics, dimensions, datasets and joins. |
| `query_metric` | Answer a question from a curated metric definition, and run it. |
| `explain_metric` | Explain what a metric means and how it is calculated. |

Eleven of those mirror the assistant's tool set; `list_workspaces` is the addition. Two more answer questions about
DuckHaven itself:

| Tool | What it does |
|---|---|
| `search_docs` | Full-text search across DuckHaven's documentation. |
| `read_doc_page` | Read one documentation page in full. |

The server also hands the agent a short set of instructions about DuckHaven's own behaviour — that the dialect is DuckDB
over Iceberg, that tables are addressed as `catalog.schema.table`, that an Iceberg table's columns come from
`describe_table` rather than `information_schema.columns`, and that the [SQL guard](../reference/sql-support.md) rejects
disallowed statements before a compute agent sees them. Agents get these wrong from general knowledge of other
platforms, and a confidently wrong answer is worse than none.

### Documentation lookup

`search_docs` and `read_doc_page` are the same pair the [assistant](assistant.md#product-knowledge) uses, and they
matter for the same reason: an agent asked "how do I query this table as it was last Tuesday?" will otherwise answer
from what it knows about Snowflake. These pages ship **inside the image**, so they describe the version you are
running rather than the latest published docs — which is a feature, since the agent will not offer you something your
deployment does not have.

They are the one exception to the everything-goes-through-the-REST-API rule, and only because there is nothing for the
exception to bypass: `docs/` is public content, identical to what the
[documentation site](https://tamasmrtn.github.io/duckhaven) serves, carrying no grants and no per-workspace
visibility. `read_doc_page` reads files off disk; `search_docs` runs
one full-text query and nothing else. They still sit behind the same front door — an anonymous caller cannot reach
them — and they take no `workspace`, because the pages are the same for everyone.

Search is ordinary lexical full-text, not semantic, so a question sharing no words with the page that answers it can
miss. When it comes back empty, that is a real answer rather than a failure, and the agent is told to say the
documentation does not cover the question instead of inventing one.

!!! note "One switch, shared with the assistant"
    `ASSISTANT_DOCS_ENABLED=false` withholds these two tools here as well as in the assistant panel — it is the
    deployment's single decision about whether AI may read the shipped documentation. They are also withheld
    automatically when the corpus is not on disk, so the agent is never offered a tool that cannot answer.

### Results are sampled, not streamed whole

`run_sql` returns at most `MCP_RESULT_ROW_CAP` rows (100 by default), trimmed further if they exceed
`MCP_RESULT_BYTE_CAP`. The rest is not lost: the result carries a `query_id` and a `cursor` that `get_query_result`
pages through. This keeps a `SELECT *` over a large table from filling the agent's context in one call.

Paging is restricted to queries **your own token ran**. The underlying rows endpoint authorizes on workspace membership
alone, so without that restriction a reader could page a colleague's result set by query id and see more than their own
grants allow.

### Lineage

`get_table_lineage` answers "what breaks if I change this?" from what DuckHaven has actually observed running, plus
anything imported from dbt — see [Lineage](lineage.md). It reads only the control plane's own store, with no Polaris
call and no compute agent, so an agent can reach for it freely.

Two properties make it safe to hand an agent, and both come from the endpoint rather than from anything the MCP server
adds. A node in a catalog you hold no grant on comes back as `redacted` — keeping its place, its distance and the
graph's shape, but carrying no names — so the agent learns that something is there without learning what. And every
cap the walk hits is reported: `truncated`, `columns_truncated`, and `hidden` for lineage outside the workspace's
catalogs. The tool tells the agent, in as many words, not to answer "nothing depends on this" from a graph carrying
any of them.

Column-level detail is off unless asked for, per node, because its size depends on how wide those tables are rather
than on the graph — so the agent names the one node it cares about after seeing the shape.

!!! note "An empty graph is not the same as no lineage"
    Lineage accumulates from queries DuckHaven runs and from dbt imports. A deployment that has done neither returns
    an empty graph, and the tool says so rather than implying the table stands alone.

## What it cannot do

- Exceed the token's access. No workspace you are not in, no catalog you were not granted, no exceptions.
- Bypass the SQL guard. The same statement allowlist that applies to the worksheet applies here.
- Write, unless an operator has explicitly enabled writes.
- Reach your data anywhere but through the REST API — not the control-plane database, not Polaris, not a compute
  agent. Its only direct database access is the documentation full-text index, which holds no customer data.
- Administer anything. There are no tools for users, tokens, grants, agents, storage backends or catalog creation —
  this is a data surface, not a management one.
- Edit a worksheet. The assistant's editor tools have no meaning over MCP; there is no editor on the other end.

## Transport and protocol

The endpoint is a single URL, `https://<your-host>/mcp`, served by the same container as the rest of DuckHaven on the
same port — there is no extra service to run and no extra port to open. It implements the current MCP specification's
**Streamable HTTP** transport (revision `2026-07-28`): one path that accepts `POST`, with no protocol-level sessions.
Local stdio transport is not offered, and would not fit — DuckHaven is a server on your network, not a program the
client launches.

Two things follow from being reachable over the network:

- **Use TLS.** The access token travels on the `Authorization` header of every request. On anything beyond a trusted
  LAN, put DuckHaven behind [a reverse proxy with TLS](../deployment/reverse-proxy-tls.md).
- **Browser origins are refused.** A request carrying an `Origin` header is rejected unless that origin is in the same
  allowlist the web app uses (`CORS_ORIGINS`) — the spec requires this, and it is what stops a web page you visit from
  driving a DuckHaven server on your network. Ordinary MCP clients send no `Origin` at all and are unaffected.

    Being on the allowlist is not enough to make a **browser-based** MCP client work, and none is supported today: the
    endpoint serves no CORS response headers and answers the preflight `OPTIONS` with a 401, so the browser blocks the
    request. The allowlist narrows who is refused; it does not open a door.

!!! note "Scope of this first version"
    The server exposes tools only — no MCP *resources*, *prompts*, or *sampling*. There is no OAuth authorization
    server: DuckHaven issues its own access tokens, and the MCP endpoint accepts one directly rather than brokering a
    third-party one, so an MCP client's "sign in with the server" flow does not apply here — configure the token
    yourself. Writes have no approval step, which is why they are off by default rather than gated.
