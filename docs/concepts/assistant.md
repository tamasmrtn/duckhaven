# AI data assistant

The AI data assistant is a governed, model-agnostic chat assistant that helps people explore a workspace's data. It can
browse catalog metadata (catalogs → schemas → tables → columns), author and run SQL against the governed Iceberg
catalogs, and explain the results — all in a conversation.

Crucially, the assistant is **not** a privileged back door. It acts as an ordinary DuckHaven service-account principal,
and every action it takes flows through the exact same enforcement as any other client. An administrator governs what
the assistant can see and do with the same grants they already use for people.

## What it can do

- **Use your curated definitions** — when the workspace has published
  [semantic models](semantic-layer.md), the assistant answers metric questions from those definitions rather than
  working the calculation out from column names. It looks up what a business term means, compiles the query from the
  stored definition, and can explain how a metric is calculated and what it excludes.
- **Browse metadata** — list the catalogs, schemas, and tables visible to it, and describe a table's columns, row count,
  and size.
- **Run SQL** — write and execute a query, then reason over a capped sample of the results. The full result set is
  available to you in the UI, exactly as if you had run the query yourself.
- **Propose editor edits** — on the worksheet, write or change the SQL in your editor as a highlighted, accept-or-reject
  suggestion (it never edits or runs on its own). If your worksheet has a non-empty text selection when you ask, the
  proposed edit is scoped to just that selection instead of rewriting the whole worksheet.
- **Explain** — summarise what it found and show the SQL it ran.
- **Answer questions about DuckHaven itself** — which SQL statements are allowed, how to query a table as of a past
  snapshot, why `DESCRIBE` rather than `information_schema.columns` reads an Iceberg table's columns, and what
  DuckHaven does not do. See [Product knowledge](#product-knowledge) below.
- **Ask when unsure** — if a request is missing something it needs to answer correctly (the grain, a time window,
  which table), it asks a short clarifying question rather than guessing.

It runs one turn at a time in a conversation, and each conversation is private to the person who started it.

## What it cannot do

- It cannot exceed the data access of its service account. If the account was granted read-only, `metadata`-tier, or
  no access to something, the assistant sees exactly that — no more.
- It cannot bypass the SQL guard. The same statement allowlist that applies to interactive queries applies here.
- It cannot silently write. Writes are off unless the service account is granted write access, and even then every
  write (INSERT/UPDATE/DELETE/DDL) must be **approved by a human** before it runs (see below).
- It has no access to DuckHaven's control-plane database, to Polaris directly, or to the compute agents directly. It
  only ever calls DuckHaven's own REST API.
- It cannot use a draft or broken semantic definition. Only *published* models reach it, and a definition whose column
  no longer exists is withheld — the assistant will say a metric is defined but currently broken rather than quietly
  substituting its own calculation.

## How governance works

The assistant is built on a general agent harness ([Pydantic AI](https://ai.pydantic.dev/)) that supplies the
conversation loop, the model abstraction, and the tool-calling machinery. DuckHaven supplies the tools, and the tools
are deliberately thin: each one is an authenticated call to DuckHaven's own REST API, made **as the service account**.

That single design choice is what makes the assistant safe. Because the tools go through the same endpoints the web app
uses, they inherit the same checks, in the same order:

```
workspace membership  →  SQL statement allowlist  →  scoped catalog grants  →  DuckDB execution
```

None of that enforcement lives in the assistant. Even if the language model were confused, or a hostile value in a
table cell tried to talk it into running something it shouldn't (a *prompt injection*), the worst it could achieve is
what the service account was already allowed to do — and every attempt is recorded.

On top of that server-side boundary, the assistant adds an **audit and UX layer**: every tool call is logged with its
arguments and outcome, and a proposed write is paused for human approval rather than executed.

## Identity

The assistant acts as one **service account**, named `Assistant` — the same kind of principal you create for
automation, described in [Service accounts & tokens](../guides/service-accounts.md). It is not configurable, and you do
not have to create it: enabling the assistant creates it on startup if it is not already there.

!!! warning "A service account you already named Assistant will be reused"
    The account is identified by the address a service account named `Assistant` would get,
    `assistant@service-account.local`. If your deployment already has one under that name — created for unrelated
    automation — enabling the assistant **adopts it**, along with every workspace membership and catalog grant it
    holds. Check for it before turning the assistant on, and rename it if the AI assistant should not inherit its
    reach.

Creating it grants nothing. It arrives with no workspace membership and no catalog grants, so until you give it some,
every question comes back denied — and the assistant panel says exactly that instead of letting you spend a turn finding
out. You govern its data access per workspace with [workspace membership](workspaces.md) and
[catalog grants](permissions.md), exactly as you would for any principal:

- Give it `reader` or a `metadata`-tier grant for a browse-and-read assistant.
- Add `writer` where you want it to be able to propose writes (still gated by approval).
- Leave it out of a workspace entirely, and it simply cannot be used there.

Because access is scoped per workspace, the same assistant can have different reach in different workspaces — broad in a
sandbox, narrow in production.

Disabling the account in the admin UI is a kill switch: the assistant stops working everywhere, a restart will not
re-enable it, and the panel says the account is unavailable rather than pointing you at workspace membership. Disabling
is also the only option once the assistant has run anything — deleting a service account that has query history is
refused, to keep the audit trail intact. Where deletion *is* still possible, it lasts until the next restart, which
recreates the account — again with no access.

When the assistant makes a call, the runtime mints a **short-lived personal access token** for the service account, uses
it for that turn, and deletes it immediately after. Nothing long-lived or reconstructable is stored, and every query the
assistant runs is attributed to the service account in the query history and audit trail.

## Write approval

When the assistant proposes a write and its service account is permitted to write, the turn pauses and the UI shows the
exact SQL for you to **approve or deny**. Only on approval does the statement run — and it still passes through the SQL
guard and grant checks like any other query. This keeps a human in the loop for anything that changes data.

## Model configuration

The assistant is model-agnostic: the provider and model are deployment configuration, not baked into the code. It
supports Anthropic, OpenAI, and Mistral natively, and any OpenAI-compatible endpoint (for example a self-hosted Ollama
or vLLM server) via a base URL — so a fully self-hosted, keyless deployment is possible. See
[Configuration](../reference/configuration.md#ai-assistant) for the settings.

## Product knowledge

The assistant is told what DuckHaven is, not just how to operate it. Its instructions carry a curated summary of the
platform's own behaviour — the DuckDB-on-Iceberg dialect and how to address tables across catalogs, the read-only
time-travel syntax, why an Iceberg table's columns come from `DESCRIBE` rather than `information_schema.columns`, which
statements the [SQL guard](../reference/sql-support.md) rejects, and which result values lose precision in transit.

The point is less that it can recite these than that it acts on them. Without the `DESCRIBE` rule it writes
`information_schema.columns` and gets a placeholder row back; without the allowlist it proposes statements the API
rejects before a compute agent ever sees them.

It is also told to answer these questions from that summary rather than from general knowledge of other data
platforms, to say plainly when it does not know instead of guessing, and to describe anything experimental or
unshipped in those words rather than as available. DuckHaven differs from Snowflake and Databricks in ways that
matter, and a confident wrong answer about one of those differences is worse than no answer.

Beyond that summary, the assistant carries an **index of this documentation** — every page's path and title, grouped
by section — and can both **search** the full text of every page and **open** any of them in full. Asked something the
summary does not cover, it searches for the pages that discuss it, reads the best match, and names the path it used
rather than reasoning from a title. Set `ASSISTANT_DOCS_ENABLED=false` to remove the section and both tools.

Search is ordinary lexical full-text — Postgres's, over the pages the image ships — not a semantic or vector index.
That is a deliberate choice for a corpus of this size, and it has an honest consequence: a question sharing no words
with the page that answers it may not find it. When search comes back empty the assistant says the documentation does
not cover the question rather than inventing an answer, which is the behaviour that matters.

!!! note "It answers for the version you are running"
    The pages the assistant reads ship inside the DuckHaven image, so they describe **your** release rather than the
    latest published documentation. If you are running an older version, that is a feature: it will not tell you about
    something your deployment does not have. It also means the assistant cannot see documentation written after your
    build — upgrade to get it.

## What it knows about your workspace

The assistant's instructions are assembled per turn from what the workspace actually has, so it is told about a
feature only where that feature is in use. A workspace with published [semantic models](semantic-layer.md) is told to
prefer them; one whose catalogs sit on [external storage](storage-backends.md) is told that credentials are vended
per query and never static; a deployment running [elastic compute](elastic-compute.md) is told that a query may wait
while an agent starts; and a workspace with more than one [agent](agents.md) is told a worksheet chooses between them.

A workspace using none of these is told nothing about any of them — not that they are switched off. That is
deliberate: the instructions stay short, and an assistant that has never heard of a feature cannot offer it to
someone who does not have it.

## Semantic grounding

Where a published semantic model covers a question, the assistant does not write the aggregation. It chooses *which*
metric and *which* dimensions; DuckHaven generates the SQL from the stored definition, so the filter, the join path and
the correct date column come from what the organization agreed rather than from the model's judgement.

Free SQL is still available and still right for anything the semantic models do not cover. It is not blocked when a
metric exists — but if a query aggregates a column that a published metric already defines, the result comes back with
a warning naming that metric, and the bypass is recorded on the tool-call audit row. How often the agreed definitions
get worked around is therefore a number you can look at rather than a hope.

!!! note "Scope"
    The assistant is a focused v1: single-agent, one conversation turn at a time, no chart generation and no retrieval
    over table *contents*, and no scheduled/unattended runs. Retrieval over semantic *definitions* — matching a
    question to a metric by name or synonym — does exist; see [Semantic layer](semantic-layer.md).
    Conversation memory is also bounded — only the most recent turns are replayed to the model, so a very long
    conversation gradually forgets its oldest messages; start a new conversation for an unrelated topic. The panel
    shows a small notice once a conversation has crossed that point.
