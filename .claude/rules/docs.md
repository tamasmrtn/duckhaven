---
paths:
  - "docs/**/*.md"
  - "mkdocs.yml"
  - ".vale/**"
  - "overrides/**"
---

# Writing DuckHaven docs

These rules hold for every page under `docs/`. The reasons and the copyable templates are in
`docs/developer/writing-docs.md`; read it before writing a new page.

Readers are self-hosting operators, analysts in the web app, and the DuckHaven assistant and MCP server. People arrive
with one question and scan for the answer. The assistant sees a one-line summary of every page, taken from the page's
first sentence, then reads whole pages, cut off at 20,000 characters.

## Pick one page type

| Type | Answers | Required sections |
|---|---|---|
| Tutorial | "Show me it working, end to end" | `## Before you begin`, numbered steps that each show the expected result, `## Next steps` |
| How-to | "How do I …?" | `## Before you begin`, steps, `## If it goes wrong` |
| Concept | "What is X, and when does it matter?" | Lead, how it works, limits, `## Related` |
| Reference | "What exactly are the options?" | Tables or fixed per-entry fields; no narrative |
| Troubleshooting | "Why did this fail?" | One `##` per symptom, quoting the exact message, each with **What it means.** and **What to do.** |

Declare it in front matter (`type: how-to`); `make docs-check` holds typed pages to their template. When a change needs
two types — a new feature usually needs a concept section and a how-to — write both and link them rather than blending
them on one page.

Place the page by subject (`querying/`, `data/`, `metadata/`, `ai/`, `access/`, `compute/`, `deployment/`,
`operations/`), not by type. `reference/` holds only tables that span subjects.

## Write the lead first

The first paragraph answers the page's question, and its first sentence stands alone in under 180 characters: it is
the assistant's summary of the page and the search snippet.

## Describe current behavior

Write as if the product had always worked this way. A paragraph narrating a change ages badly and makes the reader
compare against a version they never used.

Instead of appending:

> Results are now cached. Previously every run executed the SQL; caching was added because dashboards repeat queries.

rewrite the section in place:

> DuckHaven answers a repeated query from the result cache while nothing it reads has changed. To turn caching off…

Words like "now", "no longer", "previously" and "yet" are the tell; Vale flags them.

## Keep what predicts behavior, move what does not

Keep internals that let a reader predict what they will observe: performance, memory, errors, side effects. Move
mechanism a reader cannot act on — races, retries, why a fix was needed, code paths — to `docs/developer/`. Give a
reason only where it changes a decision the reader makes.

Instead of:

> The scheduler used to run a job twice when two replicas raced for the lock, so the tick now takes a
> transaction-scoped advisory lock.

write:

> With several API replicas, each scheduled run executes exactly once. If Postgres is unreachable, no run starts until
> it is back.

## Make examples complete

- Every how-to has a runnable example or a click path, followed by what the reader should see.
- Examples are copy-paste ready: no placeholder that points at another example. Use `⟨angle brackets⟩` only for
  values the reader must supply.
- Show a short multi-step pattern rather than a single call.
- SQL: uppercase keywords, a `;` at the end, and the canonical dataset — catalog `sales`, schema `analytics`, tables
  `orders` and `customers` — created by the example when it reads them.
- REST: the full `curl` with method, headers and body, the prerequisite that produces the token or id it uses, and the
  response.

Instead of `POST /api/workspaces/{ws}/queries with the SQL`, write:

```bash
curl -s -X POST "$DH/api/workspaces/analytics/queries" \
  -H "Authorization: Bearer $DH_TOKEN" -H "Content-Type: application/json" \
  -d '{"sql": "SELECT count(*) FROM sales.analytics.orders;"}'
```

followed by the JSON the server returns.

## Help the reader recover

Every how-to ends with `## If it goes wrong`: one entry per failure the reader is likely to hit, quoting the exact
message they see, with the cause and the fix. Messages that can appear across a subject go on that subject's
troubleshooting page.

## Keep each fact in one place

Defaults, limits, environment variables, permissions and allowed statements live in `reference/`; other pages link to
them. When a fact must appear on several pages, put it in `docs/_snippets/` and include it with `--8<-- "name.md"`.
Before finishing, search `docs/` for every fact your change touched.

Split a page only when it answers two different questions — never because it is long. A continuous page is easier to
follow than a chain of fragments. A single-question page that passes 20,000 characters goes into `TRUNCATES` in
`api/tests/unit/services/assistant/test_knowledge.py`, with a reason.

## Say who it applies to

- How-to: under `## Before you begin`, give **Applies to** (catalog kind, runtime, deployment), **You need** (the
  exact permission or role, as spelled in `api/src/api/services/permissions.py`) and **Enabled by** (the setting,
  linked to `reference/configuration.md`).
- Concept: an `!!! info "Availability"` admonition right after the lead.

## Conventions

- Second person, present tense, active voice; short sentences.
- UI labels in bold, exactly as on screen; click paths as **Compute** → **New compute**.
- Admonitions: `warning` for data loss or security, `note` for scope limits, `tip` for shortcuts. The body starts on
  the line after the title, with no blank line between them (markdownlint MD046).
- `deploy/` files link to some anchors (`#ai-assistant`, `#cost`). Keep those headings, or pin them with `{#id}`.
- Lines wrap at 120 characters.

## Before you finish

- `make docs-check` (Vale, markdownlint, structure tests, strict build) and `make docs-index` pass.
- Moved or renamed a page: add the old path to `redirect_maps` in `mkdocs.yml`.
- Reread the page as someone who has not seen the PR. Remove anything that only makes sense next to the diff.
