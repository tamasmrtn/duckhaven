# AI data assistant

The AI data assistant is a governed, model-agnostic chat assistant that helps people explore a workspace's data. It can
browse catalog metadata (catalogs → schemas → tables → columns), author and run SQL against the governed Iceberg
catalogs, and explain the results — all in a conversation.

Crucially, the assistant is **not** a privileged back door. It acts as an ordinary DuckHaven service-account principal,
and every action it takes flows through the exact same enforcement as any other client. An administrator governs what
the assistant can see and do with the same grants they already use for people.

## What it can do

- **Browse metadata** — list the catalogs, schemas, and tables visible to it, and describe a table's columns, row count,
  and size.
- **Run SQL** — write and execute a query, then reason over a capped sample of the results. The full result set is
  available to you in the UI, exactly as if you had run the query yourself.
- **Propose editor edits** — on the worksheet, write or change the SQL in your editor as a highlighted, accept-or-reject
  suggestion (it never edits or runs on its own).
- **Explain** — summarise what it found and show the SQL it ran.

It runs one turn at a time in a conversation, and each conversation is private to the person who started it.

## What it cannot do

- It cannot exceed the data access of its service account. If the account was granted read-only, `metadata`-tier, or
  no access to something, the assistant sees exactly that — no more.
- It cannot bypass the SQL guard. The same statement allowlist that applies to interactive queries applies here.
- It cannot silently write. Writes are off unless the service account is granted write access, and even then every
  write (INSERT/UPDATE/DELETE/DDL) must be **approved by a human** before it runs (see below).
- It has no access to DuckHaven's control-plane database, to Polaris directly, or to the compute agents directly. It
  only ever calls DuckHaven's own REST API.

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

Each deployment binds the assistant to one **service account** — the same kind of principal you create for automation,
described in [Service accounts & tokens](../guides/service-accounts.md). You govern its data access per workspace with
[workspace membership](workspaces.md) and [catalog grants](permissions.md), exactly as you would for any principal:

- Give it `reader` or a `metadata`-tier grant for a browse-and-read assistant.
- Add `writer` where you want it to be able to propose writes (still gated by approval).
- Leave it out of a workspace entirely, and it simply cannot be used there.

Because access is scoped per workspace, the same assistant can have different reach in different workspaces — broad in a
sandbox, narrow in production.

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

!!! note "Scope"
    The assistant is a focused v1: single-agent, one conversation turn at a time, no chart generation and no retrieval
    over table *contents*, and no scheduled/unattended runs. Conversation memory is also bounded — only the most recent
    turns are replayed to the model, so a very long conversation gradually forgets its oldest messages; start a new
    conversation for an unrelated topic.
