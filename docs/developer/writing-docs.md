# Writing docs

This page is how DuckHaven's documentation is written: the five page types, where a page goes, the rules every page
follows, and a template for each type. Coding agents load a condensed version, `.claude/rules/docs.md`, whenever they
edit `docs/`.

## Who reads these pages

- **Operators** who self-host DuckHaven and need to install, configure, upgrade and recover it.
- **Analysts and engineers** who use the web app, the CLI, dbt or dlt, and need to get a task done.
- **The assistant and the MCP server.** They hold a one-line summary of every page, taken from the page's first
  sentence, search the pages' full text, and read a page whole when they cite it. A page is cut off at 20,000
  characters for them.

Most people arrive from search with one question and scan for the answer rather than read from the top. Every rule
below follows from that.

## Why these rules

The rules come from studies of how developers use documentation and what makes it fail, not from taste.

| Rule | Evidence |
|---|---|
| One page type per page, grouped by subject | Developers know the subject of their problem but cannot predict whether the answer is in a concept, a sample or the reference, so documentation should be organized by functionality (Meng, Steinhardt & Schubert, 2019). |
| Split only when a page answers two questions | Fragmentation — one topic scattered over many pages — is as harmful as bloat (Uddin & Robillard, 2015); developers prefer a continuous narrative to a web of small pages (Robillard & DeLine, 2011). |
| Keep internals that predict behavior | Developers need to understand performance, resource use, errors and side effects to use a system well; mechanism beyond that is noise (Robillard & DeLine, 2011). |
| Complete examples with expected output | Examples are the most-used part of documentation; without them developers make more errors and succeed less often (Sohan et al., 2017; Meng et al., 2019). |
| "If it goes wrong" on every how-to | Supporting error recognition and recovery is one of the four principles of minimalist instruction, which teaches faster than conventional manuals (van der Meij & Carroll, 1995; Lazonder & van der Meij, 1993). |
| Each fact in one place | Practitioners rank clarity (88%), superfluous content (55%) and duplicated content (46%) among the documentation issues that matter most (Aghajani et al., 2020). |
| The first sentence answers the question | People scan rather than read (Nielsen Norman Group), and language models use the start of a long context better than its middle (Liu et al., 2024). |
| Describe current behavior | Outdated content is among the most common documentation failures, and most projects carry some (Uddin & Robillard, 2015; Tan, Wagner & Treude, 2024). |

## Choose a page type

| Type | Answers | Folder |
|---|---|---|
| **Tutorial** | "Show me it working, end to end." A guided path with a known-good result. | `getting-started/` |
| **How-to** | "How do I …?" One task, for someone who knows what they want. | The subject folder |
| **Concept** | "What is X, and when does it matter?" Understanding, no steps. | The subject folder |
| **Reference** | "What exactly are the options?" Complete and terse. | `reference/` if it spans subjects, else the subject folder |
| **Troubleshooting** | "Why did this fail?" Indexed by the message the reader sees. | The subject folder |

Declare the type in front matter. `make docs-check` holds typed pages to their template; untyped pages are checked once
their folder has been rewritten.

When a feature needs two types — usually a concept section and a how-to — write both and link them. A page that mixes a
concept, a procedure and a reference table serves none of its readers well.

## Put the page in its subject

| Folder | Subject |
|---|---|
| `getting-started/` | The first-run path and what DuckHaven is |
| `querying/` | Worksheets, running SQL, saved and scheduled queries, results, history, SQL sessions, dbt and dlt |
| `data/` | Catalogs, tables, DuckLake, storage backends, migration, maintenance |
| `metadata/` | Table metadata, lineage, the semantic layer |
| `ai/` | The assistant and the MCP server |
| `access/` | Users, workspaces, roles, grants, tokens, SSO |
| `compute/` | Agents, runtimes, elastic compute, scaling |
| `deployment/` | Installing, upgrading and configuring the host |
| `operations/` | Observing, drilling and troubleshooting a running deployment |
| `reference/` | Tables that span subjects: configuration, SQL, the REST API, the CLI |
| `developer/` | Working *on* DuckHaven; not read by the assistant |

To move or rename a page, `git mv` it, add the old path to `redirect_maps` in `mkdocs.yml`, and run `make docs-index`.
Old URLs then redirect, and the assistant still answers to the old path. A test fails if anything outside `docs/`
still names the old path.

## Templates

### Tutorial

~~~markdown
---
type: tutorial
---

# Install DuckHaven and run your first query

In about fifteen minutes you start the full stack, create an admin, and query a table you created yourself.

## Before you begin

- A Linux host with Docker Engine 24+ and Docker Compose v2, and 8 GB of RAM.

## 1. Start the stack

```bash
docker compose up -d
```

You should see every service reported as `Started`.

!!! tip "If a service keeps restarting"
    Run `docker compose logs ⟨service⟩` and look for the first error.

## 2. …

## What you built

## Next steps

- [Run queries](../querying/run-queries.md) — the full worksheet guide.
~~~

### How-to

~~~markdown
---
type: how-to
---

# Rotate a service-account token

Issue a new token for a service account, switch the client to it, then revoke the old one, with no window where the
client cannot authenticate.

## Before you begin

- **You need:** the `service_accounts:manage` permission.
- **Applies to:** every deployment.

## Issue the new token

1. Open **Admin** → **Service accounts**, then the account's **⋯** → **Manage tokens** → **Issue token**.
2. Copy the `dh_pat_…` value. It is shown once.

## Check that it worked

```bash
curl -s -H "Authorization: Bearer $NEW_TOKEN" "$DH/api/me"
```

The response names the service account.

## If it goes wrong

### `401 Unauthorized` after the switch

The client still sends the old token. …

## Related
~~~

### Concept

~~~markdown
---
type: concept
---

# Result cache

DuckHaven answers a repeated query from an earlier run's result while nothing the query reads has changed.

!!! info "Availability"
    On by default; can be turned off per deployment, workspace or session.

## How it works

## Limits

## Related
~~~

### Reference

~~~markdown
---
type: reference
---

# Configuration

Every DuckHaven environment variable, in one place.

| Variable | Default | Description |
|---|---|---|
| `SESSION_MAX_AGE_SECONDS` | `604800` | How long a sign-in lasts. |
~~~

### Troubleshooting

~~~markdown
---
type: troubleshooting
---

# Troubleshoot queries

Find the message you see in the results pane or the API response, then follow what to do.

## Agent not connected

**What it means.** The agent that should run the query has no open connection to the control plane.

**What to do.** Pick another agent from the engine picker, or …

## Related
~~~

## Writing rules

### The lead

The first paragraph answers the page's question. Its first sentence must stand on its own in under 180 characters,
because it becomes the page's summary for the assistant and the search snippet.

### Current behavior only

Describe the product as it is, as if it had always worked this way. "Now", "no longer", "previously" and "yet" mark a
sentence written against a version the reader may never have used; Vale flags them. Changes over time go in the
GitHub release notes.

### Internals

Keep the mechanism that lets a reader predict what they will see — how much memory a query reserves, why a statement
waits, what an error means. Move the mechanism they cannot act on — races, retry loops, why a fix was needed, code
paths — to `developer/`. Explain *why* a design is the way it is only where the reason changes a decision the reader
makes.

### Examples

- A how-to has a runnable example or a click path, followed by the result the reader should see.
- Copy-paste ready: no placeholder that refers to another example. Mark values the reader supplies as `⟨value⟩`.
- A short multi-step pattern beats a single call.
- SQL uses uppercase keywords, ends with `;`, and uses the canonical dataset: catalog `sales`, schema `analytics`,
  tables `orders` and `customers`. An example creates the tables it reads.
- REST examples show the full `curl` — method, `Authorization` and `Content-Type` headers, body — how to get the token
  or id it uses, and the response.

### Who it applies to

A how-to lists, under **Before you begin**, what it **Applies to** (catalog kind, runtime, deployment), what **You
need** (the permission or role exactly as named in `api/src/api/services/permissions.py`) and what it is **Enabled by**
(a setting, linked to [Configuration](../reference/configuration.md)). A concept page states the same in an
`!!! info "Availability"` admonition right after its lead.

### One home per fact

Defaults, limits, settings, permissions and allowed statements are stated once, in `reference/`, and linked from
elsewhere. A fact that must appear on several pages goes in `docs/_snippets/⟨name⟩.md` and is included with:

```markdown
;--8<-- "⟨name⟩.md"
```

(In this page's source the example starts with `;`, which stops MkDocs expanding it here. A real include has no `;`.)

MkDocs expands the include when it builds the site, and the assistant expands it when it reads the page.

### Conventions

- Second person, present tense, active voice. Short sentences; Vale suggests splitting one over 35 words.
- UI labels in bold, exactly as on screen. Click paths as **Compute** → **New compute**.
- Admonitions: `warning` for data loss or security, `note` for scope limits, `tip` for shortcuts. The body starts on the
  line after the title, with no blank line between them.
- Lines wrap at 120 characters.
- `deploy/` files link to some anchors (`#ai-assistant`, `#cost`). Keep those headings, or pin them with `{#id}`.
- Never edit `reference/cli.md` by hand. It is generated from the CLI's command tree in `duckhaven-clients`.

## Checks

| Command | What it checks |
|---|---|
| `make docs-serve` | Serves the site locally with live reload |
| `make docs-lint` | [Vale](https://vale.sh/docs/install) prose rules (`.vale/styles/DuckHaven/`) and markdownlint |
| `make docs-check` | `docs-lint`, the page-structure tests and a strict build (broken links and anchors fail) |
| `make docs-index` | Regenerates the assistant's page index and `docs/llms.txt`; pre-commit and CI fail when they are stale |

In CI, Vale reports only errors, on the lines a pull request adds, and fails the check when it finds one. Warnings and
suggestions appear only when you run `make docs-lint` yourself.

The page-structure tests live in `api/tests/unit/test_docs_structure.py`. Besides the templates, they check that every
redirect points at a page that exists and that nothing outside `docs/` names a moved page. The assistant's 20,000
character limit is checked by `TRUNCATES` in `api/tests/unit/services/assistant/test_knowledge.py`: a page that grows
past it fails the test until it is split or added there with a reason.

## Sources

- Aghajani, E. et al. (2020). *Software documentation: the practitioners' perspective.* ICSE.
- Lazonder, A. W. & van der Meij, H. (1993). *The minimal manual: is less really more?* Int. J. Man-Machine Studies.
- Liu, N. F. et al. (2024). *Lost in the middle: how language models use long contexts.* TACL.
- Meng, M., Steinhardt, S. & Schubert, A. (2019). *How developers use API documentation: an observation study.*
  Communication Design Quarterly.
- Robillard, M. P. & DeLine, R. (2011). *A field study of API learning obstacles.* Empirical Software Engineering.
- Sohan, S. M. et al. (2017). *A study of the effectiveness of usage examples in REST API documentation.* VL/HCC.
- Tan, W. S., Wagner, M. & Treude, C. (2024). *Detecting outdated code element references in software repository
  documentation.* Empirical Software Engineering.
- Uddin, G. & Robillard, M. P. (2015). *How API documentation fails.* IEEE Software.
- van der Meij, H. & Carroll, J. M. (1995). *Principles and heuristics for designing minimalist instruction.* Technical
  Communication.
