---
type: concept
---

# MCP server

DuckHaven speaks the [Model Context Protocol](https://modelcontextprotocol.io), so an AI agent running somewhere else —
Claude Code in your terminal, Claude Desktop, Cursor — can work with your data directly. It can list what a workspace
holds, describe a table, run SQL, and answer a question from your published
[semantic models](../metadata/semantic-layer.md), all without anyone pasting data into a chat window.

It is the same idea as the built-in [AI data assistant](assistant.md), pointed outward. The assistant is a chat panel
inside DuckHaven driven by a model DuckHaven configures; the MCP server is a door in the other direction, letting an
agent you already use bring DuckHaven into whatever it is doing — reviewing a dbt model, writing a report, debugging a
pipeline.

Turning it off is a one-line setting (`MCP_ENABLED=false`); see
[Configuration](../reference/configuration.md#mcp-server). To connect a client, see
[Connect an MCP client](connect-mcp-client.md).

## It acts as you

This is the part worth understanding, because it is what makes everything else safe.

An MCP client authenticates with a DuckHaven **access token** — the same `dh_pat_…` personal access token described in
[Service accounts & tokens](../access/service-accounts.md), issued to a person by `dh auth login` or to a service
account by an administrator. Every tool call the agent makes is then performed **as the holder of that token**, through
DuckHaven's own REST API, exactly as if that person had clicked through the web app.

So the agent sees the workspaces you are a member of and no others; reads the catalogs, schemas and tables your
[grants](../access/how-access-works.md) allow and no others; and its queries appear in the query history attributed to
you, with `duckhaven-mcp` as their client. Each call runs the [same chain of checks, in the same
order](assistant.md#how-governance-works), as every other DuckHaven client.

None of that enforcement lives in the MCP server. It holds no credentials of its own, and it never reads your data
except through those REST endpoints — never DuckHaven's control-plane database, never DuckDB, never
[Polaris](../data/catalogs.md) directly. (The one thing it does read from the database is the documentation corpus,
which is public content with no grants to enforce; see [Documentation lookup](#documentation-lookup).) The practical
consequence: **the MCP server is not a new way in.** Anything an agent can do through it, the same token could already
have done with `curl` against `/api`. Anything that token cannot do, the agent cannot do either, however the
conversation goes — which is also what bounds a [prompt injection](assistant.md#how-governance-works) to what the token
was already permitted to do.

!!! note "It differs from the assistant here"
    The assistant acts as one fixed `Assistant` service account, which an administrator grants access to centrally, and
    narrows each turn to what the person asking may also do. The MCP server has no identity of its own at all — each
    connection carries its own token, and only that token's access applies. Two people connecting the same client to
    the same deployment see different data, and revoking one person's token affects only them.

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

Twenty-one tools. Twelve work with your data:

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

Eleven of those mirror the assistant's tool set; `list_workspaces` is the addition. Seven work with
[dashboards](#dashboards):

| Tool | What it does |
|---|---|
| `get_dashboard_schema` | How to write a dashboard file: a short guide with a worked example, or the JSON Schema. |
| `list_dashboards` | The dashboards in a workspace. |
| `get_dashboard` | A dashboard's file: its parameters, its widgets, and the metrics and tables they read. |
| `validate_dashboard` | Check a file the way publishing would, without saving it. |
| `preview_dashboard` | Run a file without saving it, and read the first rows of each widget. |
| `run_dashboard` | Run a saved dashboard, or some of its widgets, and read what they show. |
| `show_chart` | Turn a result the agent ran into a dashboard widget, with its rows as a table. |

Two more answer questions about DuckHaven itself:

| Tool | What it does |
|---|---|
| `search_docs` | Full-text search across DuckHaven's documentation. |
| `read_doc_page` | Read one documentation page in full. |

The server also hands the agent a short set of instructions about DuckHaven's own behaviour — that the dialect is DuckDB
over Iceberg, that tables are addressed as `catalog.schema.table`, that an Iceberg table's columns come from
`describe_table` rather than `information_schema.columns`, that the [SQL guard](../reference/sql-support.md) rejects
disallowed statements before a compute agent sees them, and that decimals and large integers arrive as exact strings,
with a `column_types` entry in each result naming every column's type. Agents get these wrong from general knowledge
of other platforms, and a confidently wrong answer is worse than none.

### Documentation lookup

`search_docs` and `read_doc_page` are the same pair the assistant uses, and they behave identically here — the pages
ship inside the image, search is lexical rather than semantic, and an empty result is reported as "the documentation
does not cover this" rather than improvised around. [Product knowledge](assistant.md#product-knowledge) covers all of
that. They matter for the same reason too: an agent asked "how do I query this table as it was last Tuesday?" will
otherwise answer from what it knows about other data platforms.

Two things are specific to MCP. These tools are the one exception to the everything-goes-through-the-REST-API rule,
and only because there is nothing for the exception to bypass: `docs/` is public content, identical to what the
[documentation site](https://tamasmrtn.github.io/duckhaven) serves, carrying no grants and no per-workspace
visibility. `read_doc_page` reads files off disk; `search_docs` runs one full-text query and nothing else. And they
still sit behind the same front door — an anonymous caller cannot reach them — while taking no `workspace` argument,
because the pages are the same for everyone.

!!! note "One switch, shared with the assistant"
    `ASSISTANT_DOCS_ENABLED=false` withholds these two tools here as well as in the assistant panel — it is the
    deployment's single decision about whether AI may read the shipped documentation. They are also withheld
    automatically when the corpus is not on disk, so the agent is never offered a tool that cannot answer.

### Results are sampled, not streamed whole

`run_sql` returns at most `MCP_RESULT_ROW_CAP` rows, trimmed further if they exceed
`MCP_RESULT_BYTE_CAP`. The rest is not lost: the result carries a `query_id` and a `cursor` that `get_query_result`
pages through. This keeps a `SELECT *` over a large table from filling the agent's context in one call.

Paging is restricted to queries **your own token ran**. Reading any result already needs the grants its query needs,
but an agent has no reason to reach into a colleague's runs by query id, so it cannot.

### Queries name the agent that ran them

Queries from the MCP server show `duckhaven-mcp` as their client in
[History](../querying/run-queries.md#find-an-earlier-run), which says the MCP server ran them but not for which agent.
So each one also carries an `mcp.client` tag naming the agent and its version as its `User-Agent` gives them, such as
`claude-code/2.1.0`; a client that sends none gets no tag. A query from `query_metric` is also tagged with the
`semantic.model` and `semantic.metrics` it answered.

### Lineage

`get_table_lineage` answers "what breaks if I change this?" from what DuckHaven has actually observed running, plus
anything imported from dbt — see [Lineage](../metadata/lineage.md). It reads only the control plane's own store, with no
Polaris call and no compute agent, so an agent can reach for it freely.

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

### Dashboards

An agent can read a workspace's [dashboards](../querying/dashboards.md), and it can write a
[dashboard file](../reference/dashboard-spec.md) and try it out. It cannot save one. You save it in DuckHaven, by
creating a dashboard and pasting the file into its [editor](../querying/dashboards.md#editing-a-dashboard-in-the-app),
so a person reviews every dashboard before anyone else sees it.

- **Writing a file.** `get_dashboard_schema` returns a short guide by default: what each widget type needs from its
  query, how parameters work, the limits, and a worked example. The JSON Schema lists the allowed keys, but it cannot
  say that a line needs a grain, and rules like that are where agents go wrong.
- **Checking it.** `validate_dashboard` runs the checks publishing runs, as you, and returns every problem with its
  path, line and column. `preview_dashboard` runs the file as you. It returns each widget's first rows, how the widget
  would draw, and advice on what would make it read badly, such as a title holding a number. Both need the writer
  role in the workspace, as [previews](../reference/rest-api.md#previewing-a-dashboard-file) and validations do in
  the REST API.
- **Reading one.** `run_dashboard` runs a saved dashboard as you, exactly as its page does, so the numbers match what
  you would see there. Each widget says which window its numbers cover, and which parameters did and did not filter
  it. A reader can run a published dashboard; running the draft needs the writer role.
- **Charts.** An MCP client cannot draw DuckHaven's charts, so `show_chart` draws nothing. It checks that a result the
  agent ran can be drawn as a widget. It returns that widget, ready to go in a dashboard file, with the rows as a
  Markdown table to show you. As with paging, only a query your own token ran can be used.

Results are cut to fit an agent's context. A call returns at most 12 widgets and at most 20 rows of each, 5 unless the
agent asks for more. Cells are cut to 80 characters, and all the widgets together to about 6 KB. The rest is not lost:
each widget carries a `query_id` that `get_query_result` pages.

!!! note "A dashboard's text is someone else's words"
    A dashboard's titles, descriptions and text widgets are written by people in the workspace. The server's
    instructions and the tools tell the agent to report that text as its author's words, and never to follow it as
    instructions. A hostile dashboard is bounded the same way as everything else here: the agent can do only what
    your token can, and no tool saves, publishes or deletes a dashboard.

#### Resources and prompts

For a client that offers resources, the guide and the schema are resources too, and so is each dashboard's file:

| Resource | What it holds |
|---|---|
| `duckhaven://dashboard/guide` | The authoring guide, as Markdown. |
| `duckhaven://dashboard/schema` | The JSON Schema of a dashboard file. |
| `duckhaven://workspaces/{workspace}/dashboards/{name}` | A dashboard's file as YAML, comments included, read with your access. |

Two prompts start the common tasks. Claude Code, for one, offers them as slash commands.

- `build_dashboard` takes a `workspace`, a `topic` and an optional `audience`. It has the agent read the guide, find
  the metrics, write the file, validate it and preview it, and then give you the file to save.
- `explain_dashboard` takes a `workspace`, a dashboard `name` and an optional `question`. It has the agent read the
  dashboard, run the widgets the question is about, and dig further with `query_metric`.

## What it cannot do

- Exceed the token's access. No workspace you are not in, no catalog you were not granted, no exceptions.
- Bypass the SQL guard. The same statement allowlist that applies to the worksheet applies here.
- Write, unless an operator has explicitly enabled writes.
- Reach your data anywhere but through the REST API — not the control-plane database, not Polaris, not a compute
  agent. Its only direct database access is the documentation full-text index, which holds no customer data.
- Administer anything. There are no tools for users, tokens, grants, agents, storage backends or catalog creation —
  this is a data surface, not a management one.
- Edit a worksheet. The assistant's editor tools have no meaning over MCP; there is no editor on the other end.
- Save, publish or delete a dashboard. An agent writes and tries out a file, and a person saves it.

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
    The server exposes tools, the [dashboard resources and prompts](#resources-and-prompts), and no *sampling*.
    There is no OAuth authorization
    server: DuckHaven issues its own access tokens, and the MCP endpoint accepts one directly rather than brokering a
    third-party one, so an MCP client's "sign in with the server" flow does not apply here — configure the token
    yourself. Writes have no approval step, which is why they are off by default rather than gated.

## Related

- [Connect an MCP client](connect-mcp-client.md) — the setup walkthrough.
- [AI assistant](assistant.md) — the same governance, pointed inward.
- [Service accounts & tokens](../access/service-accounts.md) — issuing the token a client authenticates with.
- [Permissions](../access/how-access-works.md) — what a token can reach.
