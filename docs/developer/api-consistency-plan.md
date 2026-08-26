# DuckHaven REST API Consistency Plan

!!! note "Status"
    This is a working plan, not a description of shipped behaviour. Phases 1-7 in
    [Phased delivery](#5-phased-delivery) are executed in order; anything marked for a
    later phase is **not** implemented yet. The conventions in
    [Conventions](#2-conventions) are the normative source and are published separately as
    the [API conventions reference](../reference/api-conventions.md).

## Context

The DuckHaven REST surface has grown feature by feature to **125 paths / 161 operations /
17 tag groups**, mounted at `/api`. It works, but it has drifted in ways that are cheap to
fix now and expensive later: the OpenAPI schema documents almost no errors, four operations
declare a status code they do not return, one resource family is served at two URLs, and
path-parameter naming has no rule. A first-party CLI (`dh`) is about to be designed against
this exact surface (`CLI_IMPLEMENTATION_PROMPT.md`, not yet executed — `docs/developer/cli-plan.md`
does not exist), so the surface should be settled before a second consumer is generated from it.

The intended outcome is a surface that generates good third-party clients, plus a written
convention set that stops the next 161 operations drifting the same way. **Consistency is
the goal, not novelty.** Of 161 operations, **49 are unchanged**, **79 change only in the
schema** (parameter template names, declared status codes, metadata — no URL a client sends
changes), and **33 change on the wire**. Nothing in this plan changes what a handler does.

---

## 1. Corrected audit

The Appendix B script was re-run against `api_app.openapi()`. **Every structural count in
Appendix A reproduces exactly**: 125 paths, 161 operations, the path-parameter histogram,
the status-code-by-method table, 31 bare arrays vs 4 envelopes, 155×`422` + 6×none, and the
`["semantic","semantic"]` tag pair. Corrections and additions follow.

### 1.1 Corrections to Appendix A

| # | Finding as stated | Correction |
|---|---|---|
| **A1** | "10 path pairs, **20 of 161** operations" | 10 path pairs, but **26 operations** (13 per side). Counting by path undercounts the multi-method paths (`/schemas` and `/schemas/{schema}/tables` each carry GET+POST; `/schemas/{schema}/tables/{table}` carries GET+DELETE). |
| **A1** | Framed as an open question | **Already decided in the code.** `api/src/api/routers/schemas.py:821-856` registers the pairs from a `_ROUTES` table against `_CANON = "/workspaces/{ws}/catalogs/{catalog}/schemas"` and `_LEGACY = "/workspaces/{ws}/schemas"`, with the comment *"canonical (catalog-scoped) + legacy (default catalog)"*. `web/src/api/schemas.ts:29-35` says the same thing in its own comment. The plan ratifies this rather than re-deciding it. Consequence: collapsing is a **~3-line server change and one client helper**, not 26 edits. |
| **A1** | Implies the shim is a pure duplicate | **It is not, in one place.** `GET /workspaces/{ws}/schemas/{schema}/tables/{table}/health` (`routers/maintenance.py:128`) exists **only** on the legacy form and has no catalog-scoped twin. Removing the shim without adding that twin deletes a live route. |
| **A3** | Lists `.../conversations/{id}/messages` among POSTs that "create or mutate and should not" return 200 | **Not a defect.** Both `messages` and `approvals` return `StreamingResponse(media_type="text/event-stream")` (`routers/assistant.py:214,239`). `200` is correct for an SSE stream. The real defect is that the schema declares no media type for either. |
| **A4** | "`{catalog}` is a name" | **`{catalog}` is a slug.** `resolve_catalog(db, workspace_id, catalog_slug)` matches `Catalog.slug` (`services/workspace.py:305`). Only `{schema}` and `{table}` are bare names, and correctly so — they are Iceberg identifiers DuckHaven does not own. |
| **A4** | "`{ws}` is a slug" | **`{ws}` is slug-*or*-UUID.** `get_workspace(db, slug_or_id)` tries `uuid.UUID(...)` first and falls back to slug (`services/workspace.py:207`). The polymorphism is deliberate and useful; it is the *name* `ws` that is the defect, not the type. |
| **A10** | `List All Catalogs` and `Get Agent Access` read oddly | **Both summaries are accurate.** `list_all_catalogs` does return every catalog in the deployment (`routers/catalogs.py:104-112`); `get_agent_access` does return the agent's access mode, grants and principals (`routers/admin/agent_access.py:85`). They need *descriptions*, not renames. The genuine offender is the `*_workspace_detail` family — `Get/Update/Delete Workspace Detail` — where "Detail" describes nothing (`GET /workspaces/{ws}` returns the same `WorkspaceOut` the list returns). |
| **Filter drift** (brief, not Appendix A) | "sessions, schedule runs, users, agents use different names and defaults for the same concepts" | **Sessions already conform.** `GET /workspaces/{ws}/sql/sessions` uses `status` (aliased list), `user_id`, `agent_id`, `since`, `limit` — the same vocabulary as `GET /workspaces/{ws}/queries`, just a subset (`routers/sql_sessions.py:335-344`). `/admin/users` and `/agents` take **no** filters at all, so there is nothing to reconcile. The real drift is narrower and listed in §1.2. |
| **Blast radius** (brief) | "133 literal `/api...` call sites across the 22 modules in `web/src/api/`" | **127 of the 133 are in `web/src/mock/handlers/` (20 files), not `web/src/api/`.** Only 6 `/api` literals sit outside the mock handlers (2 in `api/setup.ts`, 2 in `api/assistant.ts`, 1 in `api/client.ts`, which prefixes every path with `/api`, 1 in `features/auth`). The client modules use **98 bare-path literals (77 distinct)** because `request()` supplies the prefix. Also: **the MSW handlers live under `web/src/mock/`, not `web/tests/`** — `web/tests/mock/server.ts` only imports them. A rename is still a three-place change; the three places are `api/src/api/routers/`, `web/src/api/` (bare paths), and `web/src/mock/handlers/` (prefixed paths). |

### 1.2 Additions — findings no script would surface

| # | Finding | Count | Example |
|---|---|---|---|
| **B1** | **The schema declares a status code the handler does not return.** Four handlers mutate `response.status_code` at runtime; OpenAPI records only the decorator value. Generated clients will not handle the real code. | 4 | `PUT /workspaces/{ws}/catalogs/{catalog}/grants` declares `200`, returns `201` on create (`routers/grants.py:178`); `PUT /admin/agents/{agent_id}/grants` same (`admin/agent_access.py:158`); `POST /workspaces/{ws}/saved-queries` declares `201`, returns `200` on overwrite-by-name (`routers/queries.py:719`); `POST /workspaces/{ws}/sql/sessions` declares `201`, returns `202` when `on_wait_timeout="continue"` (`routers/sql_sessions.py:265`). |
| **B2** | **Redirects declared as `200`.** Both OIDC routes return `RedirectResponse(..., 303)`; the schema says `200` with no content. | 2 | `GET /auth/oidc/{provider}/callback` (`routers/oidc.py:58,75,89`). |
| **B3** | **SSE streams undeclared.** Two POSTs return `text/event-stream`; the schema declares neither the media type nor the event shape. | 2 | `POST /workspaces/{ws}/assistant/conversations/{conversation_id}/messages`. |
| **B4** | **Sibling routes name the same identifier two ways.** `PATCH .../metrics/{metric_name}` and `DELETE .../metrics/{name}` address the identical resource. | 1 pair | `routers/semantic.py:723` vs `:979`. |
| **B5** | **An unbounded page size.** `GET /queries/{query_id}/rows` takes `limit: int = 100` with **no `le=`** — every other list endpoint caps it. | 1 | `routers/queries.py:581`. |
| **B6** | **`limit` defaults and caps are arbitrary.** `100/500` (queries, sessions, schedule-runs), `50/200` (schedule runs), `200/500` (session statements), `10/50` (semantic search), `100/uncapped` (query rows). | 6 | — |
| **B7** | **`status` has three different types.** `list[str]` aliased (queries, sessions); `str` or `None` (semantic models, `routers/semantic.py:296`); `str` defaulting to `"open"` (recommendations, `routers/maintenance.py:195`). | 3 | — |
| **B8** | **Two search endpoints disagree on whether `q` is required.** `/workspaces/{ws}/search` takes `q: str = Query("")`; `/workspaces/{ws}/semantic/search` takes `q: str = Query(min_length=1)`. | 2 | `routers/search.py:49` vs `routers/semantic.py:1052`. |
| **B9** | **The `admin` tag holds 37 operations across 5 unrelated resources** (agents, users, service accounts, storage, maintenance), because `main.py:211-216` passes `tags=["admin"]` to six `include_router` calls. A generated client gets one 37-method `AdminApi` class. Meanwhile `agents` is a 1-operation tag. | 1 tag | `main.py:211-216`. |
| **B10** | **`operationId`s are auto-generated and collide semantically.** `GET /agents` and `GET /admin/agents` are both `list_agents` with summary `List Agents`, distinguished only by the path suffix FastAPI appends (`list_agents_agents_get` vs `list_agents_admin_agents_get`). No route sets an explicit `operation_id`. | 161 | — |
| **B11** | **The two grant `PUT`s key the resource in the body, not the path.** Both are genuine idempotent upserts, so `PUT` is defensible, but identity-in-body is not the pattern `PUT /admin/users/{user_id}/workspaces/{ws}` already establishes in the same codebase. | 2 | `routers/grants.py:120`. |
| **B12** | **Error bodies split 32/68.** 32 raise sites use `detail={"error": ..., "detail": ...}` (concentrated in `queries.py`, `semantic.py`, `sql_sessions.py`); 68 use `detail="<string>"`. Confirms A8 and quantifies it. | 100 | — |
| **B13** | **Routers raise 78×404, 25×409, 21×503, 11×403, 1×401 — none documented.** This is the concrete measure of A7: the schema documents `422` and nothing else, while the code demonstrably returns five other classes. | 136 raises | — |
| **B14** | **Authentication is described as per-call parameters, and no `securitySchemes` is declared.** Because `get_current_user` takes `session: str | None = Cookie(...)` and `authorization: str | None = Header(...)`, FastAPI documents both as ordinary optional parameters on every operation that depends on it. The schema declares no `securitySchemes` and no `security`, so a generated client makes the caller pass a cookie and a bearer header by hand on every call instead of configuring credentials once. | 151 of 161 | `GET /workspaces/{ws}` documents `session` and `authorization` alongside `ws` (`deps.py:29-32`). |
| **B15** | **Two literal segments occupy an identifier slot whose sibling is a name, not a UUID** — the §2.1.6 rule the other four A6 cases satisfy. A catalog named `attach` or an Iceberg namespace named `refresh-stats` would be unreachable. Surfaced by the conformance test, not by reading. `refresh-stats` is also **scoped wrongly**: the handler iterates every schema in the catalog (`schemas.py:372-382`), so it is a catalog-level operation living under `/schemas`. | 3 paths | `POST /workspaces/{ws}/catalogs/attach` shadows `{catalog}`; `POST /workspaces/{ws}/catalogs/{catalog}/schemas/refresh-stats` shadows `{schema}`. |

### 1.3 Findings confirmed exactly as written

A2 (31 bare / 4 paged / two envelope shapes), A3 status histogram, A5 (`pat`/`pats`/`pat`),
A6 (four literal siblings of `{agent_id}`), A7, A8, A9, A11 (~44 verb segments), A12.

### 1.4 Method-semantics sweep result

- **All 10 `PATCH`es are genuinely partial**, by three different idioms: `model_fields_set`
  (`workspaces.py:110`), `exclude_unset` (`schedules.py:131`, `queries.py:755`), and
  `is not None` guards (`admin/users.py:99`). No violations.
- **All 4 `PUT`s are idempotent.** Two key identity in the body (B11) — documented exception,
  not a rename.
- **One `GET` has side effects**: `GET /auth/oidc/{provider}/callback` establishes a session.
  This is required by the OIDC authorization-code flow; **ratified as a documented exception.**
- **No `POST` that should be a `PUT`** except `POST /workspaces/{ws}/catalogs/attach`, which
  is an idempotent membership write whose `DELETE` counterpart already lives at
  `/workspaces/{ws}/catalogs/{catalog}`.

---

## 2. Conventions

*Written to be lifted into `docs/reference/api-conventions.md` as the standard for future
endpoints. Every rule below is mechanically checkable and is asserted by the conformance
test in §6.*

### 2.1 Path structure

1. The API is mounted at `/api`. All paths in this document are relative to that mount.
2. A collection is a **plural noun**; a member is that collection plus one identifier segment.
3. **Scope-nesting and global addressing coexist, deliberately.** A resource with a
   server-generated globally unique id is *created and listed* under its owning scope
   (`POST /workspaces/{workspace}/queries`) and *read and mutated* at its global address
   (`GET /queries/{query_id}`). This is not drift — it avoids re-validating the scope on
   every read and keeps ids portable. It applies to queries, SQL sessions, catalogs, and
   catalog migrations. **A12 is ratified, not fixed.**
4. A **verb segment** is permitted as a `POST` sub-resource when the operation is a genuine
   state transition that is not a field write and not naturally idempotent:
   `/cancel`, `/restart`, `/terminate`, `/disconnect`, `/publish`, `/deprecate`, `/validate`,
   `/dismiss`, `/revoke-sessions`, `/refresh-stats`, `/recount`, `/scan`, `/compile`,
   `/login`, `/logout`, `/bootstrap`. It is **not** permitted when a method or a field write
   already expresses it — which is why `POST .../catalogs/attach` becomes
   `PUT /workspaces/{workspace}/catalogs/{catalog}`.
5. A **single-field `PATCH` sub-resource** (`/access-mode`) is permitted only while the parent
   resource has no `PATCH` representation. Both current uses qualify and are consistent with
   each other; leave them.
6. A **literal sibling of an id segment** (`/admin/agents/metrics` beside `/admin/agents/{agent_id}`)
   is permitted only when the id is a **UUID**, so no literal can ever collide. All four A6
   cases qualify. Leave them; the conformance test enforces the rule going forward.

### 2.2 Identifiers

**The rule:** a path parameter is named for the **singular of the collection segment
immediately preceding it**, and carries the `_id` suffix **if and only if** its value is a UUID.

| Identifier kind | Used for | Naming | Examples |
|---|---|---|---|
| Slug | Human-authored, user-visible, stable | singular, no suffix | `{workspace}`, `{catalog}`, `{model}`, `{metric}` |
| UUID | Server-generated and opaque | singular + `_id` | `{query_id}`, `{agent_id}`, `{service_account_id}` |
| External name | Identifiers DuckHaven does not own | singular, no suffix | `{schema}`, `{table}` |

Consequences: `{ws}`→`{workspace}`, `{sa_id}`→`{service_account_id}`, `{sq_id}`→`{saved_query_id}`,
`{rec_id}`→`{recommendation_id}`, `{backend_id}`→`{storage_backend_id}`, `{slug}`→`{model}`,
`{metric_name}`/`{name}`→`{metric}` (fixing B4), `{name}`→`{dataset}`/`{dimension}`/`{relationship}`.

Two documented exceptions:

- **`{provider}`** follows `/imports`, not `/providers`. The producer name *is* the identifier
  of the import set. Kept.
- **`{catalog}` (slug, workspace-scoped) and `{catalog_id}` (UUID, global) address the same
  resource.** This is rule 2.1.3 working as intended, not a defect. Kept.

`{workspace}` accepts a slug **or** a UUID; this is documented behaviour, not an accident.

### 2.3 Methods

- `GET` — safe and side-effect-free. One documented exception: `GET /auth/oidc/{provider}/callback`,
  which the OIDC spec requires to establish a session.
- `POST` — create, or a non-idempotent action from the 2.1.4 list, or open a stream.
- `PUT` — idempotent full replace. Identity belongs in the path; the two grant upserts are a
  documented exception where the key is composite and not URL-safe.
- `PATCH` — partial update. Absent fields are untouched; the handler must distinguish "absent"
  from "null" (`model_fields_set` or `exclude_unset`).
- `DELETE` — remove. `204` with no body, unless the operation is a **batch** whose result is a
  count, in which case `200` with a report (the single current case,
  `DELETE /workspaces/{workspace}/lineage/imports`, is ratified under this rule).

### 2.4 Status codes

- `201` + `Location` for resource creation. `202` for accepted async work. `204` for empty
  success. `200` otherwise.
- A **batch operation returning a report** (`refresh-stats`, `recount`, lineage/semantic
  imports, `scan`) is `200`, not `201` — it creates no addressable resource.
- A **pure function** (`compile`, `validate`) is `200`.
- A **stream** is `200` with its media type declared.
- **Every status a handler can actually return must be declared**, including conditional ones.
  This is what B1/B2/B3 violate today.

### 2.5 Pagination

- **Unbounded collections** — those that grow with usage — return
  `{"items": [...], "cursor": <opaque|null>, "has_more": <bool>}` and accept `cursor` and
  `limit`. `QueriesPageOut` is the reference implementation and is unchanged.
- **Bounded collections** — those bounded by deployment topology or by an already-bounded
  parent — return a bare array and are **exempt**, by this list, which is exhaustive:
  `/workspaces`, `/workspaces/{workspace}/members`, `/catalogs`,
  `/workspaces/{workspace}/catalogs`, `/agents`, `/admin/agents`, `/admin/agents/metrics`,
  `/admin/storage-backends`, `/admin/users/{user_id}/workspaces`,
  `/admin/service-accounts/{service_account_id}/pats`, `/workspaces/{workspace}/schedules`,
  `/workspaces/{workspace}/semantic/models`,
  `/workspaces/{workspace}/semantic/models/{model}/metrics/{metric}/dimensions`,
  `/workspaces/{workspace}/catalogs/{catalog}/schemas`.
- **`RowsPageOut` is not a collection envelope.** It is a result grid (`rows`, `columns`,
  `column_schema`, `cursor`, `total`) describing tabular query output, and is deliberately a
  different type. Documented, unchanged.
- **Search returns a report, not a page**: `{"items": [...], ...diagnostics}`, no cursor,
  truncated by `limit`. `SemanticSearchOut`'s `hits` is renamed to `items`; its `ambiguous`
  and `broken` diagnostics stay.
- **`limit` is `default=100, ge=1, le=1000`** everywhere unless a per-endpoint cost justifies
  otherwise, and the justification is written in the docstring. This fixes B5 and B6.

### 2.6 Filtering and sorting

`GET /workspaces/{workspace}/queries` is **canonical**. Any list endpoint offering these
concepts uses these names and types:

| Concept | Parameter | Type |
|---|---|---|
| State filter | `status` | repeated string (`list[str]`), no default |
| Principal | `user_id`, `agent_id` | UUID |
| Time window | `since`, `until` | ISO-8601 datetime |
| Free text | `q` | string |
| Sort | `sort`, `dir` | enum, `dir ∈ {asc, desc}` |
| Page | `cursor`, `limit` | opaque string, int |

`status` is **always** a repeated string with no default (fixing B7 — `/maintenance/recommendations`
loses its implicit `"open"` default, which moves the default into the SPA where it belongs;
`/semantic/models` widens from `str | None`). `q` is **always required** where the endpoint
is a search (fixing B8).

### 2.7 Errors

**A single bespoke envelope for every 4xx/5xx:**

```json
{ "error": "sql_not_allowed", "message": "DDL is not permitted in this session.", "details": {} }
```

- `error` — stable machine-readable snake_case code. The SPA already branches on this field.
- `message` — human-readable, safe to display.
- `details` — optional object; endpoint-specific structured context.

**It is implemented as one exception handler**, alongside the existing
`@api_app.exception_handler(PolarisError)` at `main.py:181`, normalising `HTTPException`:
a `dict` detail maps straight through (the 32 structured sites already carry `error`), and a
`str` detail becomes `{"error": <derived-from-status>, "message": <the string>}` with codes
`unauthorized`/`forbidden`/`not_found`/`conflict`/`unprocessable_content`/`internal_error`.
**No raise site has to change**; routers can be upgraded to specific `error` codes
incrementally afterwards. `web/src/api/client.ts:19-31` loses its unwrapper and reads
`body.message ?? body.error`.

RFC 9457 was evaluated and **rejected**: `application/problem+json` trips content-type
sniffing in several generated clients, none of its field names (`type`/`title`/`detail`/`instance`)
match what the SPA reads today, and it earns its keep only with a maintained type-URI
registry this project has no consumer for. The deciding factor is what the SPA and generated
clients consume without special-casing.

### 2.8 OpenAPI metadata

1. Every operation sets an explicit `operation_id`: `snake_case`, `verb_subject`, globally
   unique, stable across releases. `GET /agents` → `list_usable_agents`;
   `GET /admin/agents` → `list_all_agents` (fixing B10).
2. Every operation has a `summary` (imperative sentence fragment, ≤ 60 chars) and a
   `description` (the handler docstring).
3. Every operation declares the errors it can return. Minimum set by construction:
   `401` wherever `get_current_user` is a dependency; `403` wherever `require_permission`,
   `require_agent_tier`, or `assert_workspace_member` is; `404` on every path with an id
   segment; `409` and `503` where the handler raises them.
4. One tag per operation, no duplicates. `admin` splits into `admin-agents`, `admin-users`,
   `admin-service-accounts`, `admin-storage`, `admin-maintenance` (fixing B9); the
   `["semantic","semantic"]` pair is fixed by dropping the local `tags=` at `semantic.py:1335`,
   which duplicates the `include_router` tag at `main.py:204`.
5. Deprecated operations carry `deprecated: true` and name their replacement in the description.

---

## 3. Full route mapping — all 161 operations

**Legend.** `no` = unchanged. `schema` = **schema-only**: the parameter *template name*,
declared status codes, or metadata change; **no URL a client sends changes**, and no SPA or
MSW edit is required. `**wire**` = the request or response changes on the wire.

**Totals: 49 unchanged · 79 schema-only · 33 wire-breaking.**

| Current | Proposed | Breaking | Note |
|---|---|---|---|
| `GET /admin/agents` | unchanged | no |  |
| `POST /admin/agents/bootstrap` | → 201 | **wire** | mints a credential |
| `GET /admin/agents/compute-options` | unchanged | no |  |
| `POST /admin/agents/elastic` | unchanged | no |  |
| `GET /admin/agents/metrics` | unchanged | no |  |
| `GET /admin/agents/{agent_id}` | unchanged | no |  |
| `DELETE /admin/agents/{agent_id}` | unchanged | no |  |
| `GET /admin/agents/{agent_id}/access` | unchanged | no |  |
| `PATCH /admin/agents/{agent_id}/access-mode` | unchanged | no | §2.1.5 exception |
| `DELETE /admin/agents/{agent_id}/credential` | unchanged | no |  |
| `POST /admin/agents/{agent_id}/disconnect` | unchanged | no |  |
| `PUT /admin/agents/{agent_id}/grants` | → 200+201 | schema | B1: declares the 201 it already returns |
| `DELETE /admin/agents/{agent_id}/grants/{grant_id}` | unchanged | no |  |
| `GET /admin/agents/{agent_id}/monitoring` | unchanged | no |  |
| `POST /admin/agents/{agent_id}/restart` | unchanged | no |  |
| `POST /admin/agents/{agent_id}/terminate` | unchanged | no |  |
| `GET /admin/maintenance/policy` | unchanged | no |  |
| `PUT /admin/maintenance/policy` | unchanged | no |  |
| `POST /admin/maintenance/scan` | unchanged | no | §2.4 batch report |
| `GET /admin/service-accounts` | → `{items, cursor, has_more}` | **wire** |  |
| `POST /admin/service-accounts` | unchanged | no |  |
| `PATCH /admin/service-accounts/{sa_id}` | `PATCH /admin/service-accounts/{service_account_id}` | schema |  |
| `DELETE /admin/service-accounts/{sa_id}` | `DELETE /admin/service-accounts/{service_account_id}` | schema |  |
| `POST /admin/service-accounts/{sa_id}/pat` | `POST /admin/service-accounts/{service_account_id}/pats` → 201 | **wire** | A5: plural, matches the GET |
| `DELETE /admin/service-accounts/{sa_id}/pat/{pat_id}` | `DELETE /admin/service-accounts/{service_account_id}/pats/{pat_id}` | **wire** | A5: plural |
| `GET /admin/service-accounts/{sa_id}/pats` | `GET /admin/service-accounts/{service_account_id}/pats` | schema |  |
| `GET /admin/storage-backends` | unchanged | no |  |
| `POST /admin/storage-backends` | unchanged | no |  |
| `DELETE /admin/storage-backends/{backend_id}` | `DELETE /admin/storage-backends/{storage_backend_id}` | schema |  |
| `POST /admin/storage-backends/{backend_id}/health` | `POST /admin/storage-backends/{storage_backend_id}/health` | schema | probe has side effects; POST is correct |
| `GET /admin/users` | → `{items, cursor, has_more}` | **wire** |  |
| `POST /admin/users` | unchanged | no |  |
| `PATCH /admin/users/{user_id}` | unchanged | no |  |
| `POST /admin/users/{user_id}/revoke-sessions` | unchanged | no |  |
| `GET /admin/users/{user_id}/workspaces` | unchanged | no | bounded, exempt |
| `PUT /admin/users/{user_id}/workspaces/{ws}` | `PUT /admin/users/{user_id}/workspaces/{workspace}` | schema | reference PUT pattern |
| `DELETE /admin/users/{user_id}/workspaces/{ws}` | `DELETE /admin/users/{user_id}/workspaces/{workspace}` | schema |  |
| `GET /agents` | unchanged | no |  |
| `POST /auth/login` | unchanged | no |  |
| `POST /auth/logout` | unchanged | no |  |
| `GET /auth/methods` | unchanged | no |  |
| `GET /auth/oidc/{provider}/callback` | → 303 | schema | B2; §2.3 GET exception |
| `GET /auth/oidc/{provider}/login` | → 303 | schema | B2 |
| `GET /catalogs` | unchanged | no |  |
| `DELETE /catalogs/{catalog_id}` | unchanged | no |  |
| `GET /catalogs/{catalog_id}/migrations` | → `{items, cursor, has_more}` | **wire** |  |
| `POST /catalogs/{catalog_id}/migrations` | unchanged | no |  |
| `GET /catalogs/{catalog_id}/migrations/{migration_id}` | unchanged | no |  |
| `POST /catalogs/{catalog_id}/migrations/{migration_id}/cancel` | unchanged | no |  |
| `GET /catalogs/{catalog_id}/migrations/{migration_id}/logs` | → `{items, cursor, has_more}` | **wire** | `after` → `cursor` |
| `GET /healthz` | unchanged | no |  |
| `GET /maintenance/health` | unchanged | no |  |
| `GET /maintenance/recommendations` | → `{items, cursor, has_more}` | **wire** | B7: `status` loses its `"open"` default |
| `POST /maintenance/recommendations/{rec_id}/dismiss` | `POST /maintenance/recommendations/{recommendation_id}/dismiss` | schema |  |
| `GET /me` | unchanged | no |  |
| `GET /metrics` | unchanged | no |  |
| `GET /queries/{query_id}` | unchanged | no |  |
| `DELETE /queries/{query_id}` | unchanged | no |  |
| `GET /queries/{query_id}/profile` | unchanged | no |  |
| `GET /queries/{query_id}/rows` | unchanged | no | B5: `limit` gains `le=1000` (a cap, not a contract change) |
| `GET /readyz` | unchanged | no |  |
| `POST /setup/admin` | → 201 | **wire** | creates the first admin |
| `GET /setup/status` | unchanged | no |  |
| `GET /sql/sessions/{session_id}` | unchanged | no |  |
| `DELETE /sql/sessions/{session_id}` | unchanged | no |  |
| `POST /sql/sessions/{session_id}/staging-files` | → 201 | **wire** | creates files |
| `GET /sql/sessions/{session_id}/statements` | → `{items, cursor, has_more}` | **wire** |  |
| `POST /sql/sessions/{session_id}/statements` | unchanged | no |  |
| `GET /version` | unchanged | no |  |
| `GET /workspaces` | unchanged | no | bounded, exempt |
| `POST /workspaces` | unchanged | no |  |
| `GET /workspaces/{ws}` | `GET /workspaces/{workspace}` | schema | A10: summary loses "Detail" |
| `PATCH /workspaces/{ws}` | `PATCH /workspaces/{workspace}` | schema | A10 |
| `DELETE /workspaces/{ws}` | `DELETE /workspaces/{workspace}` | schema | A10 |
| `GET /workspaces/{ws}/assistant/conversations` | `GET /workspaces/{workspace}/assistant/conversations` → `{items, cursor, has_more}` | **wire** |  |
| `POST /workspaces/{ws}/assistant/conversations` | `POST /workspaces/{workspace}/assistant/conversations` | schema |  |
| `GET /workspaces/{ws}/assistant/conversations/{conversation_id}` | `GET /workspaces/{workspace}/assistant/conversations/{conversation_id}` | schema |  |
| `PATCH /workspaces/{ws}/assistant/conversations/{conversation_id}` | `PATCH /workspaces/{workspace}/assistant/conversations/{conversation_id}` | schema |  |
| `DELETE /workspaces/{ws}/assistant/conversations/{conversation_id}` | `DELETE /workspaces/{workspace}/assistant/conversations/{conversation_id}` | schema |  |
| `POST /workspaces/{ws}/assistant/conversations/{conversation_id}/approvals` | `POST /workspaces/{workspace}/…/approvals` → 200 SSE | schema | B3: declares `text/event-stream` |
| `POST /workspaces/{ws}/assistant/conversations/{conversation_id}/messages` | `POST /workspaces/{workspace}/…/messages` → 200 SSE | schema | B3 |
| `GET /workspaces/{ws}/assistant/status` | `GET /workspaces/{workspace}/assistant/status` | schema |  |
| `GET /workspaces/{ws}/catalogs` | `GET /workspaces/{workspace}/catalogs` | schema | bounded, exempt |
| `POST /workspaces/{ws}/catalogs` | `POST /workspaces/{workspace}/catalogs` | schema |  |
| `POST /workspaces/{ws}/catalogs/attach` | `PUT /workspaces/{workspace}/catalogs/{catalog}` → 200/201 | **wire** | A11: symmetric with the existing DELETE |
| `DELETE /workspaces/{ws}/catalogs/{catalog}` | `DELETE /workspaces/{workspace}/catalogs/{catalog}` | schema |  |
| `PATCH /workspaces/{ws}/catalogs/{catalog}/access-mode` | `PATCH /workspaces/{workspace}/catalogs/{catalog}/access-mode` | schema | §2.1.5 exception |
| `GET /workspaces/{ws}/catalogs/{catalog}/grants` | `GET /workspaces/{workspace}/catalogs/{catalog}/grants` | schema |  |
| `PUT /workspaces/{ws}/catalogs/{catalog}/grants` | `PUT /workspaces/{workspace}/catalogs/{catalog}/grants` → 200+201 | schema | B1/B11 |
| `DELETE /workspaces/{ws}/catalogs/{catalog}/grants/{grant_id}` | `DELETE /workspaces/{workspace}/catalogs/{catalog}/grants/{grant_id}` | schema |  |
| `GET /workspaces/{ws}/catalogs/{catalog}/schemas` | `GET /workspaces/{workspace}/catalogs/{catalog}/schemas` | schema | canonical form |
| `POST /workspaces/{ws}/catalogs/{catalog}/schemas` | `POST /workspaces/{workspace}/catalogs/{catalog}/schemas` | schema |  |
| `POST /workspaces/{ws}/catalogs/{catalog}/schemas/refresh-stats` | `POST /workspaces/{workspace}/catalogs/{catalog}/refresh-stats` | **wire** | B15: catalog-scoped operation, and frees the `{schema}` slot |
| `DELETE /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}` | `DELETE /workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}` | schema |  |
| `GET /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables` | `GET /workspaces/{workspace}/…/tables` | schema | see Risk R2 |
| `POST /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables` | `POST /workspaces/{workspace}/…/tables` | schema |  |
| `GET /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}` | `GET /workspaces/{workspace}/…/tables/{table}` | schema |  |
| `DELETE /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}` | `DELETE /workspaces/{workspace}/…/tables/{table}` | schema |  |
| `GET /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/lineage` | `GET /workspaces/{workspace}/…/lineage` | schema |  |
| `POST /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/recount` | `POST /workspaces/{workspace}/…/recount` | schema |  |
| `GET /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/sample` | `GET /workspaces/{workspace}/…/sample` | schema | `RowsPageOut`, §2.5 |
| `GET /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/semantic` | `GET /workspaces/{workspace}/…/semantic` | schema | A9: tag `["semantic","semantic"]` → `["semantic"]` |
| `GET /workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/snapshots` | `GET /workspaces/{workspace}/…/snapshots` | schema | see Risk R2 |
| **— new —** | `GET /workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}/health` | additive | **canonical twin the legacy-only health route needs** (A1 correction) |
| `GET /workspaces/{ws}/health` | `GET /workspaces/{workspace}/health` | schema |  |
| `POST /workspaces/{ws}/lineage/imports` | `POST /workspaces/{workspace}/lineage/imports` | schema | §2.4 batch report → 200 |
| `DELETE /workspaces/{ws}/lineage/imports` | `DELETE /workspaces/{workspace}/lineage/imports` | schema | A3: the lone DELETE-200, ratified by §2.3 |
| `POST /workspaces/{ws}/lineage/imports/{provider}` | `POST /workspaces/{workspace}/lineage/imports/{provider}` | schema | `{provider}` exception, §2.2 |
| `GET /workspaces/{ws}/members` | `GET /workspaces/{workspace}/members` | schema | bounded, exempt |
| `POST /workspaces/{ws}/members` | `POST /workspaces/{workspace}/members` | schema |  |
| `GET /workspaces/{ws}/queries` | `GET /workspaces/{workspace}/queries` | schema | **canonical filter set, §2.6** |
| `POST /workspaces/{ws}/queries` | `POST /workspaces/{workspace}/queries` | schema |  |
| `GET /workspaces/{ws}/saved-queries` | `GET /workspaces/{workspace}/saved-queries` → `{items, cursor, has_more}` | **wire** |  |
| `POST /workspaces/{ws}/saved-queries` | `POST /workspaces/{workspace}/saved-queries` → 201+200 | schema | B1: declares the 200 it returns on overwrite |
| `PATCH /workspaces/{ws}/saved-queries/{sq_id}` | `PATCH /workspaces/{workspace}/saved-queries/{saved_query_id}` | schema |  |
| `DELETE /workspaces/{ws}/saved-queries/{sq_id}` | `DELETE /workspaces/{workspace}/saved-queries/{saved_query_id}` | schema |  |
| `GET /workspaces/{ws}/schedule-runs` | `GET /workspaces/{workspace}/schedule-runs` → `{items, cursor, has_more}` | **wire** |  |
| `GET /workspaces/{ws}/schedules` | `GET /workspaces/{workspace}/schedules` | schema | bounded, exempt |
| `POST /workspaces/{ws}/schedules` | `POST /workspaces/{workspace}/schedules` | schema |  |
| `PATCH /workspaces/{ws}/schedules/{schedule_id}` | `PATCH /workspaces/{workspace}/schedules/{schedule_id}` | schema |  |
| `DELETE /workspaces/{ws}/schedules/{schedule_id}` | `DELETE /workspaces/{workspace}/schedules/{schedule_id}` | schema |  |
| `GET /workspaces/{ws}/schedules/{schedule_id}/runs` | `GET /workspaces/{workspace}/schedules/{schedule_id}/runs` → `{items, cursor, has_more}` | **wire** | B6: `limit` 50/200 → 100/1000 |
| `GET /workspaces/{ws}/schemas` | deprecated → `GET /workspaces/{workspace}/catalogs/{catalog}/schemas` | **wire** | A1 legacy shim; removed at v2 |
| `POST /workspaces/{ws}/schemas` | deprecated → `POST …/catalogs/{catalog}/schemas` | **wire** | A1 |
| `POST /workspaces/{ws}/schemas/refresh-stats` | deprecated → `POST …/catalogs/{catalog}/schemas/refresh-stats` | **wire** | A1 |
| `DELETE /workspaces/{ws}/schemas/{schema}` | deprecated → `DELETE …/catalogs/{catalog}/schemas/{schema}` | **wire** | A1 |
| `GET /workspaces/{ws}/schemas/{schema}/tables` | deprecated → `GET …/catalogs/{catalog}/schemas/{schema}/tables` | **wire** | A1 |
| `POST /workspaces/{ws}/schemas/{schema}/tables` | deprecated → `POST …/catalogs/{catalog}/schemas/{schema}/tables` | **wire** | A1 |
| `GET /workspaces/{ws}/schemas/{schema}/tables/{table}` | deprecated → `GET …/tables/{table}` | **wire** | A1 |
| `DELETE /workspaces/{ws}/schemas/{schema}/tables/{table}` | deprecated → `DELETE …/tables/{table}` | **wire** | A1 |
| `GET /workspaces/{ws}/schemas/{schema}/tables/{table}/health` | deprecated → `GET …/catalogs/{catalog}/schemas/{schema}/tables/{table}/health` | **wire** | **the twin above must exist first** |
| `GET /workspaces/{ws}/schemas/{schema}/tables/{table}/lineage` | deprecated → `GET …/tables/{table}/lineage` | **wire** | A1 |
| `POST /workspaces/{ws}/schemas/{schema}/tables/{table}/recount` | deprecated → `POST …/tables/{table}/recount` | **wire** | A1 |
| `GET /workspaces/{ws}/schemas/{schema}/tables/{table}/sample` | deprecated → `GET …/tables/{table}/sample` | **wire** | A1 |
| `GET /workspaces/{ws}/schemas/{schema}/tables/{table}/semantic` | deprecated → `GET …/tables/{table}/semantic` | **wire** | A1 + A9 |
| `GET /workspaces/{ws}/schemas/{schema}/tables/{table}/snapshots` | deprecated → `GET …/tables/{table}/snapshots` | **wire** | A1 |
| `GET /workspaces/{ws}/search` | `GET /workspaces/{workspace}/search` → `{items, …}` | **wire** | B8: `q` becomes required |
| `POST /workspaces/{ws}/semantic/compile` | `POST /workspaces/{workspace}/semantic/compile` | schema | §2.4 pure function |
| `DELETE /workspaces/{ws}/semantic/imports` | `DELETE /workspaces/{workspace}/semantic/imports` | schema |  |
| `POST /workspaces/{ws}/semantic/imports/{provider}` | `POST /workspaces/{workspace}/semantic/imports/{provider}` | schema |  |
| `GET /workspaces/{ws}/semantic/models` | `GET /workspaces/{workspace}/semantic/models` | schema | bounded, exempt; B7: `status` → `list[str]` |
| `POST /workspaces/{ws}/semantic/models` | `POST /workspaces/{workspace}/semantic/models` | schema |  |
| `GET /workspaces/{ws}/semantic/models/{slug}` | `GET /workspaces/{workspace}/semantic/models/{model}` | schema |  |
| `PATCH /workspaces/{ws}/semantic/models/{slug}` | `PATCH /workspaces/{workspace}/semantic/models/{model}` | schema |  |
| `DELETE /workspaces/{ws}/semantic/models/{slug}` | `DELETE /workspaces/{workspace}/semantic/models/{model}` | schema |  |
| `POST /workspaces/{ws}/semantic/models/{slug}/datasets` | `POST …/models/{model}/datasets` | schema |  |
| `DELETE /workspaces/{ws}/semantic/models/{slug}/datasets/{name}` | `DELETE …/models/{model}/datasets/{dataset}` | schema |  |
| `POST /workspaces/{ws}/semantic/models/{slug}/deprecate` | `POST …/models/{model}/deprecate` | schema |  |
| `POST /workspaces/{ws}/semantic/models/{slug}/dimensions` | `POST …/models/{model}/dimensions` | schema |  |
| `DELETE /workspaces/{ws}/semantic/models/{slug}/dimensions/{name}` | `DELETE …/models/{model}/dimensions/{dimension}` | schema |  |
| `POST /workspaces/{ws}/semantic/models/{slug}/metrics` | `POST …/models/{model}/metrics` | schema |  |
| `PATCH /workspaces/{ws}/semantic/models/{slug}/metrics/{metric_name}` | `PATCH …/models/{model}/metrics/{metric}` | schema | **B4** |
| `GET /workspaces/{ws}/semantic/models/{slug}/metrics/{metric_name}/dimensions` | `GET …/models/{model}/metrics/{metric}/dimensions` | schema | bounded, exempt |
| `DELETE /workspaces/{ws}/semantic/models/{slug}/metrics/{name}` | `DELETE …/models/{model}/metrics/{metric}` | schema | **B4**: was `{name}`, now matches its PATCH sibling |
| `POST /workspaces/{ws}/semantic/models/{slug}/publish` | `POST …/models/{model}/publish` | schema |  |
| `POST /workspaces/{ws}/semantic/models/{slug}/relationships` | `POST …/models/{model}/relationships` | schema |  |
| `DELETE /workspaces/{ws}/semantic/models/{slug}/relationships/{name}` | `DELETE …/models/{model}/relationships/{relationship}` | schema |  |
| `POST /workspaces/{ws}/semantic/models/{slug}/validate` | `POST …/models/{model}/validate` | schema | §2.4 pure function |
| `GET /workspaces/{ws}/semantic/search` | `GET /workspaces/{workspace}/semantic/search` → `{items, …}` | **wire** | `hits` → `items` |
| `GET /workspaces/{ws}/sql-metadata` | `GET /workspaces/{workspace}/sql-metadata` | schema |  |
| `GET /workspaces/{ws}/sql/sessions` | `GET /workspaces/{workspace}/sql/sessions` → `{items, cursor, has_more}` | **wire** | filters already canonical |
| `POST /workspaces/{ws}/sql/sessions` | `POST /workspaces/{workspace}/sql/sessions` → 201+202 | schema | B1: declares the 202 it already returns |

---

## 4. Scope cut line

You chose **one breaking release**, and **deprecate the legacy shim**. Those reconcile as
follows: everything non-breaking ships first across Phases 1–4 with `api_version` staying `1`;
Phase 3 marks the shim `deprecated: true` so anyone reading the schema in the interim is warned;
Phase 5 makes **every** wire change in a single release and bumps `api_version` to `2`. There is
no multi-release concurrent-surface window — only a schema-level warning ahead of the cut.

| Bucket | Contents | Rationale |
|---|---|---|
| **Fix now** (Phases 1–4, `api_version` 1) | All OpenAPI metadata (B9, B10, A9, A10); documented `401`/`403`/`404`/`409`/`503` (A7/B13); declaring the real status codes (B1/B2/B3); `securitySchemes` and hidden auth parameters (B14); `limit` caps and defaults (B5/B6); all path-parameter template renames (A4/B4); the new canonical table-health route; `deprecated: true` on the 13 shim ops | None of it changes a URL, request, or response body. Highest value per unit of risk, and it is what makes generated clients usable. |
| **Fix at the `api_version` 2 bump** (Phase 5) | Error envelope (A8/B12); pagination on 11 collections + 2 searches (A2); `pat`→`pats` (A5); `attach`→`PUT` (A11); 3 POST→201 (A3); shim removal (A1); `status`/`q` parameter types (B7/B8); the `refresh-stats` move (B15) | Every item changes the wire. Batching them into one release means one migration for consumers instead of several. |
| **Accepted inconsistency** (documented, unchanged) | A12 global/nested coexistence; `{catalog}` vs `{catalog_id}`; A6 literal siblings; the two `/access-mode` PATCHes; `POST .../health`; `RowsPageOut` as a distinct grid type; the two body-keyed grant `PUT`s; 14 bounded bare-array collections; `GET /auth/oidc/{provider}/callback` side effects | Each is either a coherent pattern that a rule can describe, or a change whose SPA and MSW churn exceeds what consistency buys. §2 states the rule for each so it reads as a decision, not an oversight. |

---

## 5. Phased delivery

Each phase is independently shippable and mergeable. Per `CLAUDE.md` §5, each is its own
branch with logically grouped commits; no PR is opened unless asked. Steps are given in the
`CLAUDE.md` §4 form, `N. [Step] → verify: [check]`; they are laid out as a table because the
final phase's command is longer than the repo's 120-character line limit and must be verbatim.

| # | Step and verification |
|---|---|
| 1 | **Land this document and the conventions reference.** Copy the plan to `docs/developer/api-consistency-plan.md`; write `docs/reference/api-conventions.md` from §2; add both to `mkdocs.yml` nav. — `1. Land the plan and conventions reference → verify: mkdocs build --strict` |
| 2 | **Add the OpenAPI conformance test** (§8.1) with every convention it can already assert marked `xfail(strict=True)`, so the ruleset is executable before anything moves. — `2. Add the OpenAPI conformance test → verify: make test-api` |
| 3 | **OpenAPI metadata, non-breaking.** Explicit `operation_id` + `summary` + `description` on all 161 routes; split the `admin` tag into five; drop the duplicate `tags=` at `semantic.py:1335`; rename the `*_workspace_detail` handlers; declare `401`/`403`/`404`/`409`/`503` per §2.8.3 via shared `responses=` constants; declare the real status codes for B1/B2/B3; declare `securitySchemes` (`cookieAuth`, `bearerAuth`) and mark the `session`/`authorization` parameters `include_in_schema=False`; cap `/queries/{query_id}/rows` at `le=1000`; normalise `limit` defaults. — `3. Fix OpenAPI metadata and declared responses → verify: make test-api` |
| 4 | **Path-parameter renames, the canonical table-health route, and deprecation flags.** Rename every template per §2.2 across the 30 router modules; add `GET .../catalogs/{catalog}/schemas/{schema}/tables/{table}/health`; add `deprecated=True` to the 14 legacy registrations (`schemas.py:856`, `semantic.py:1329-1335`, `maintenance.py:128`). — `4. Rename path parameters and deprecate the shim → verify: make test-api` plus a schema assertion that no path contains `{ws}`, `{slug}`, `{sa_id}`, `{sq_id}`, `{rec_id}`, `{backend_id}`, `{metric_name}` or a bare `{name}` |
| 5 | **The breaking release.** `API_VERSION` 1 → 2 (`health.py:27`). In one branch: the error envelope handler in `main.py` and the `client.ts` unwrapper removal; the `{items, cursor, has_more}` envelope on the 11 collections and the 2 searches; `pat` → `pats`; `POST .../catalogs/attach` → `PUT .../catalogs/{catalog}`; the 3 `POST` → `201`; deletion of the `_LEGACY` registrations; `status`/`q` parameter types; `.../schemas/refresh-stats` → `.../catalogs/{catalog}/refresh-stats`. SPA and MSW handlers updated in the same branch. — `5. Ship the breaking changes and bump api_version → verify: make test-api && make test-web` |
| 6 | **Docs.** Update `docs/reference/rest-api.md`, the guides naming renamed routes, and the `api_version` note; regenerate any embedded route examples. — `6. Update the docs for the new surface → verify: mkdocs build --strict` |
| 7 | `7. Run tests and pre-commit → verify: make test-api && make test-web && pre-commit run --all-files && mkdocs build --strict` |

> **Note on `make test`.** The web layer fails on `main` independently of this work; run
> `make test-web` as the real gate rather than the combined `make test`.

---

## 6. Blast radius per phase

| Phase | Routers touched | `web/src/api/` | `web/src/mock/handlers/` | Docs |
|---|---|---|---|---|
| 1 | — | — | — | new `docs/reference/api-conventions.md`; `mkdocs.yml` |
| 2 | — | — | — | — |
| 3 | all 30 (+ `main.py` tags) | — | — | — |
| 4 | all 30 with `{ws}` etc. (~24 files); `maintenance.py` gains a route | — | — | — |
| 5 | `main.py` (handler), `queries.py`, `sql_sessions.py`, `schedules.py`, `catalogs.py`, `maintenance.py`, `search.py`, `semantic.py`, `admin/users.py`, `admin/service_accounts.py`, `schemas.py` (delete `_LEGACY` loop), `setup.py`, `admin/agents.py` | `client.ts`, `catalogs.ts`, `queries.ts`, `schedules.ts`, `sql-sessions.ts`, `search.ts`, `semantic.ts`, `users.ts`, `service-accounts.ts`, `maintenance.ts`, `assistant.ts`, `catalog-migrations.ts`, `schemas.ts` (drop the `base()` fallback), `setup.ts` — **14 of 22** | `catalogs.ts`, `queries.ts`, `schedules.ts`, `sql-sessions.ts`, `search.ts`, `semantic.ts`, `users.ts`, `service-accounts.ts`, `maintenance.ts`, `assistant.ts`, `catalog-migrations.ts`, `schemas.ts`, `setup.ts` — **13 of 20** | — |
| 6 | — | — | — | `docs/reference/rest-api.md`; `docs/guides/{service-accounts,import-dbt-lineage,import-dbt-semantics,define-metrics,connect-idp}.md`; `docs/concepts/{sql-sessions,query-execution}.md` |
| 7 | — | — | — | — |

**Phases 1–4 require zero SPA or MSW edits** — that is the point of the schema-only /
wire-breaking split in §3, and it is why 79 of 112 changes cost nothing outside `api/`.

---

## 7. `api_version` schedule

- **`api_version` stays `1` through Phases 1–4.** Nothing in them changes a URL, a request,
  or a response body. Path-parameter *template* names, tags, `operationId`s, summaries and
  declared responses are schema artefacts; renaming `{ws}` to `{workspace}` leaves
  `/api/workspaces/acme/queries` byte-identical on the wire. Bumping for them would make the
  integer meaningless.
- **Phase 5 bumps `API_VERSION` to `2`** (`api/src/api/routers/health.py:27`) and carries every
  wire change at once.
- **Deprecation window:** the 13 legacy shim operations plus the legacy table-health route are
  marked `deprecated: true` in Phase 4 and removed in Phase 5. There is no concurrent-surface
  release, because you chose a single breaking cut — the window is the interval between the
  Phase 4 and Phase 5 releases, and Phase 5's release notes must name it.

**On `/v2/` path prefixes.** Not proposed. The project already committed to `api_version` as a
single integer served from `GET /api/version`, documented in `docs/reference/rest-api.md:31-32`.
A path prefix would double the routing surface, force every SPA and MSW literal to carry a
version segment, and give a consumer no way to discover the version without guessing a URL — the
`/version` endpoint already answers that in one unauthenticated request. The existing mechanism
is the better one and it is already built.

---

## 8. Test strategy

### 8.1 The conformance test — `api/tests/unit/test_openapi_conformance.py`

New file, sitting beside the existing `test_app_wiring.py`. It walks `api_app.openapi()` once
and asserts the §2 rules, so a new endpoint that drifts **fails CI instead of shipping**.

| Assertion | Rule | Introduced in |
|---|---|---|
| Every operation has an explicit `operation_id`, unique, `snake_case`, not FastAPI's auto-generated `<fn>_<path>_<method>` form | §2.8.1 | Phase 3 |
| Every operation has a non-empty `summary` and `description` | §2.8.2 | Phase 3 |
| Every operation has exactly one tag, drawn from an allow-list | §2.8.4 | Phase 3 |
| No operation documents `session` or `authorization` as a parameter; the schema declares `securitySchemes` and every authenticated operation references one | §2.8.6 | Phase 3 |
| Every operation whose route depends on `get_current_user` declares `401`; on `require_permission`/`require_agent_tier` declares `403` | §2.8.3 | Phase 3 |
| Every path containing `{…}` declares `404` | §2.8.3 | Phase 3 |
| Every path parameter is the singular of its preceding collection segment, with `_id` iff its schema is `format: uuid`; exceptions read from an explicit allow-list (`provider`) | §2.2 | Phase 4 |
| No path parameter matches `^(ws|slug|[a-z]{2,3}_id)$` | §2.2 | Phase 4 |
| A literal path segment may sit beside a sibling id segment only when that id is `format: uuid` | §2.1.6 | Phase 5 |
| Every `GET` on a collection path returns either the standard envelope or a schema on the exemption list — and the exemption list in the test **is** the one in §2.5 | §2.5 | Phase 5 |
| Every `4xx`/`5xx` response references the single `ErrorOut` schema | §2.7 | Phase 5 |
| Every `POST` returning `201` declares a `Location` header | §2.4 | Phase 5 |
| `limit` is `ge=1, le≤1000` wherever it appears | §2.5 | Phase 3 |

Phase 2 adds the file with each assertion `pytest.mark.xfail(strict=True)`; each later phase
removes the `xfail` it satisfies. `strict=True` means an assertion that starts passing early
also fails the build, so the phases cannot silently overlap.

### 8.2 Behavioural tests

- **Phase 3** — `api/tests/unit/routers/test_health.py`: assert `GET /version` still returns
  `api_version == 1`.
- **Phase 4** — no behavioural change; the existing 26 router test modules must pass untouched,
  which is itself the proof that template renames are not wire changes.
- **Phase 5**, per `CLAUDE.md` §6, regression tests written alongside the change:

| Test module | Asserts |
|---|---|
| `test_errors.py` (new) | Every status class returns `{error, message, details}`; a `str` detail and a `dict` detail both normalise; `PolarisError` still maps correctly |
| `test_queries.py`, `test_sql_sessions.py`, `test_schedules.py`, `test_maintenance.py`, `test_catalog_migrations.py`, `admin/test_users.py`, `test_service_accounts.py` | Envelope shape and cursor round-trip on each newly paged collection |
| `test_catalogs.py` | `PUT /workspaces/{workspace}/catalogs/{catalog}` returns `201` on first attach, `200` on re-attach, and `DELETE` still detaches |
| `test_schemas.py` | The legacy paths return `404`; the canonical paths and the new table-health route return `200` |
| `test_service_accounts.py` | `/pats` works, `/pat` returns `404` |
| `test_setup.py`, `admin/test_agents.py`, `test_sql_sessions.py` | The three new `201`s |

### 8.3 Vitest / MSW

Only Phase 5 touches the SPA. Per renamed or reshaped route, three edits stay in lockstep:
the router, the `web/src/api/` module, and the `web/src/mock/handlers/` module.

| Change | `web/src/api/` | `web/src/mock/handlers/` | Vitest |
|---|---|---|---|
| Error envelope | `client.ts` — delete `errorMessage`'s unwrapper, read `body.message ?? body.error` | all 20 error fixtures | `web/tests/api/client.test.ts` — rewrite the shape cases |
| Pagination ×13 | `queries.ts`, `sql-sessions.ts`, `schedules.ts`, `users.ts`, `service-accounts.ts`, `maintenance.ts`, `assistant.ts`, `catalog-migrations.ts`, `search.ts`, `semantic.ts` | same 10 | the feature tests that render each list |
| `attach` → `PUT` | `catalogs.ts` | `catalogs.ts` | catalog-attach feature test |
| `pat` → `pats` | `service-accounts.ts` | `service-accounts.ts` | service-account feature test |
| Shim removal | `schemas.ts` — delete the `base()` fallback branch, make `catalog` required | `schemas.ts` | schema/table browser tests |
| `q` required | `search.ts` | `search.ts` | search feature test |

`web/tests/mock/contract.test.ts` already exists and is the natural place to assert that every
handler URL corresponds to a real route; extending it to read the generated OpenAPI paths would
catch SPA/router divergence automatically. Flagged as a Phase 5 stretch, not a requirement.

---

## 9. Docs plan

| Page | Change | Phase |
|---|---|---|
| `docs/reference/api-conventions.md` | **New.** §2 verbatim. The standard every future endpoint is reviewed against. | 1 |
| `mkdocs.yml` | Add the new page to nav | 1 |
| `docs/developer/api-consistency-plan.md` | **New.** This document. | 1 |
| `docs/reference/rest-api.md` | Rewrite "Resource groups" for the five split admin tags; add an "Errors" section for the envelope; add a "Pagination" section for the envelope and the exemption rule; add a "Migrating to `api_version` 2" section listing all 32 wire changes; update the `api_version` example to `2` | 6 |
| `docs/guides/service-accounts.md` | `/pat` → `/pats` | 6 |
| `docs/guides/import-dbt-lineage.md`, `import-dbt-semantics.md`, `define-metrics.md` | Any `{ws}`/`{slug}` in a documented URL template | 6 |
| `docs/guides/connect-idp.md` | Note the `303` on the OIDC routes | 6 |
| `docs/concepts/sql-sessions.md`, `query-execution.md` | Session and statement list responses become enveloped | 6 |
| `docs/operations/*`, `docs/deployment/*` | Verified: they reference `/healthz`, `/readyz`, `/metrics`, `/version` only — **all unchanged**, no edit needed | — |

---

## 10. Risks and open questions

- **R1 — The CLI effort collides with Phase 5.** `CLI_IMPLEMENTATION_PROMPT.md` exists and is
  unexecuted (`docs/developer/cli-plan.md` does not exist). Its Appendix A is an endpoint
  inventory generated from *today's* schema, so a CLI planned before Phase 5 would bake in
  `/pat`, the bare-array lists, the `detail` error shape, and the legacy shim. **Recommendation:
  run the CLI planning session after Phase 4 lands**, or hand that session §2 and §3 of this
  document as input. Phases 1–4 do not affect it at all.
- **R2 — Tables and snapshots are excluded from pagination, unverified.**
  `GET .../schemas/{schema}/tables` and `.../snapshots` proxy Polaris/Iceberg listings. Whether
  Polaris exposes a page token is **not verified in this session**; I did not read
  `services/polaris.py`'s list methods closely enough to assert either way. They are exempt for
  now. If Polaris does page, they belong in §2.5's unbounded set and should be added at the next
  `api_version` bump, not retrofitted into Phase 5.
- **R3 — External consumers are invisible.** The SPA and the planned CLI are the only consumers
  I can see. PATs exist specifically so CI and unattended tooling can call the API
  (`docs/guides/service-accounts.md`), so third-party scripts may well be calling
  `/pat`, the bare-array lists, or the legacy shim. Nothing in the repo can enumerate them.
  Phase 5's release notes must carry the full §3 wire-breaking list.
- **R4 — Splitting the `admin` tag renames generated client classes.** It is not a wire change
  and needs no `api_version` bump, but anyone generating a client from the schema gets
  `AdminAgentsApi` instead of `AdminApi`. Worth a release-note line even though it lands in
  Phase 3.
- **R5 — `status` on `/maintenance/recommendations` loses its `"open"` default.** §2.6 requires
  no default; today an unparameterised call returns only open recommendations. The SPA must send
  `?status=open` explicitly or its list silently widens. Small, but easy to miss — it is in the
  Phase 5 SPA checklist for that reason.
- **R6 — `POST .../storage-backends/{id}/health` reads like a field write.** It is a probe with
  side effects (it vends credentials), so `POST` is right, but `/health` as the noun is
  misleading. `/health-checks` would read better. **Left unchanged** — a cosmetic rename with
  SPA and MSW churn and no consistency gain. Raising it so the decision is visible rather than
  overlooked.
- **Open question — SSE event schemas.** Phase 3 declares `text/event-stream` on the two
  assistant routes, but the *event* payloads are produced by `stream_turn`/`resume_turn` in the
  assistant service, which this session did not read. Documenting the event shapes properly is
  worth doing and is **not** included in any phase above; it needs its own scoping.
