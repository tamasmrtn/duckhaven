# API conventions

The rules every DuckHaven REST endpoint follows. They exist so that the surface stays
predictable as it grows, and so that a client generated from the OpenAPI schema is usable
without hand-written special cases.

New endpoints are reviewed against this page. Where a rule has an exception, the exception
is written down here — an undocumented deviation is a defect. Every rule that can be checked
against the generated schema is also asserted by `api/tests/unit/test_openapi_conformance.py`,
so an endpoint that drifts fails CI rather than shipping.

## Path structure

The API is mounted at `/api`. Every path below is relative to that mount.

A collection is a **plural noun**; a member is that collection plus one identifier segment.

### Scope-nesting and global addressing coexist

This is deliberate, not drift. A resource with a server-generated globally unique id is
*created and listed* under its owning scope, and *read and mutated* at its global address:

```
POST /workspaces/{workspace}/queries      # created in a scope
GET  /queries/{query_id}                  # read by global id
```

The scoped form is where authorization naturally lives and where a listing is meaningful.
The global form avoids re-validating the scope on every read and keeps ids portable — an id
you were handed is enough to address the resource. This applies to queries, SQL sessions,
catalogs, and catalog migrations.

### Verb segments

A verb segment is permitted as a `POST` sub-resource when the operation is a genuine state
transition that is neither a field write nor naturally idempotent:

`/cancel` · `/restart` · `/terminate` · `/disconnect` · `/publish` · `/deprecate` ·
`/validate` · `/dismiss` · `/revoke-sessions` · `/refresh-stats` · `/recount` · `/scan` ·
`/compile` · `/login` · `/logout` · `/bootstrap`

It is **not** permitted when a method or a field write already expresses the operation.
Attaching a catalog to a workspace is a membership write, so it is
`PUT /workspaces/{workspace}/catalogs/{catalog}` — not `POST .../catalogs/attach`.

### Single-field PATCH sub-resources

A sub-resource that exists only to write one field (`/access-mode`) is permitted **only
while the parent resource has no `PATCH` representation**. If the parent later gains one,
the field moves into it and the sub-resource is deprecated.

### Literal segments beside id segments

A literal path segment may sit beside a sibling id segment — `/admin/agents/metrics` next to
`/admin/agents/{agent_id}` — **only when that id is a UUID**, so no literal value can ever
collide with a real identifier. With a slug or a name in that position, the literal is a
routing hazard and is not allowed.

## Identifiers

**The rule:** a path parameter is named for the **singular of the collection segment
immediately preceding it**, and carries the `_id` suffix **if and only if** its value is a
UUID.

| Kind | Used for | Naming | Examples |
|---|---|---|---|
| Slug | Human-authored, user-visible, stable | singular, no suffix | `{workspace}`, `{catalog}`, `{model}`, `{metric}` |
| UUID | Server-generated and opaque | singular + `_id` | `{query_id}`, `{agent_id}`, `{service_account_id}` |
| External name | Identifiers DuckHaven does not own | singular, no suffix | `{schema}`, `{table}` |

`{schema}` and `{table}` are Iceberg identifiers. DuckHaven does not mint them and cannot
slugify them, so they appear as the names the catalog holds.

Two documented exceptions:

- **`{provider}`** follows `/imports`, not `/providers`. The producer name *is* the
  identifier of the import set it asserted.
- **`{catalog}` (slug, workspace-scoped) and `{catalog_id}` (UUID, global)** address the same
  resource. That is scope-nesting and global addressing working as intended, above.

`{workspace}` accepts a **slug or a UUID**. This is supported behaviour: a slug is what a
person types, a UUID is what a stored reference holds, and both resolve to the same
workspace.

## Methods

| Method | Meaning |
|---|---|
| `GET` | Safe and side-effect-free |
| `POST` | Create, a non-idempotent action from the verb list above, or open a stream |
| `PUT` | Idempotent full replace; identity belongs in the path |
| `PATCH` | Partial update; absent fields are untouched |
| `DELETE` | Remove |

`PATCH` handlers must distinguish *absent* from *null* — use `model_fields_set` or
`model_dump(exclude_unset=True)`, never a bare `is None` check on a nullable field.

`DELETE` returns `204` with no body, unless the operation is a **batch** whose result is a
count, in which case it returns `200` with a report.

**Documented exceptions:**

- `GET /auth/oidc/{provider}/callback` establishes a session. The OIDC authorization-code
  flow requires the IdP to redirect the browser there with a `GET`; there is no compliant
  alternative.
- The two grant upserts (`PUT .../catalogs/{catalog}/grants`,
  `PUT /admin/agents/{agent_id}/grants`) key the target in the request body rather than the
  path, because the key is composite and not URL-safe. They are idempotent, so `PUT` is
  correct; only the location of the key is exceptional.

## Status codes

- `201` for resource creation
- `202` for accepted async work
- `204` for empty success
- `200` for everything else

A `201` must leave the caller able to reach what was made: **return the created resource, or
send a `Location` header.** Returning the resource is preferred and is what every creating
endpoint here does — it saves the follow-up request a bare `Location` would cost. `Location`
is added on top only where the resource's address cannot be derived from the request path:
`POST /workspaces/{workspace}/sql/sessions` creates a session that is then operated at
`/sql/sessions/{session_id}`, so it says so. RFC 9110 makes `Location` a SHOULD, and a
header naming the wrong URL is worse than no header.

Three cases that look like creation but are not:

- A **batch operation returning a report** (`refresh-stats`, `recount`, lineage and semantic
  imports, `scan`) is `200`. It creates no addressable resource.
- A **pure function** (`compile`, `validate`) is `200`.
- A **stream** is `200`, with its media type declared.

**Every status a handler can actually return must be declared in the schema**, including
conditional ones. A handler that sets `response.status_code` at runtime must list that code
alongside the decorator's.

## Pagination

**Unbounded collections** — those that grow with usage — return:

```json
{ "items": [], "cursor": null, "has_more": false }
```

and accept `cursor` and `limit`. Cursors are opaque and keyset-based, not offsets: these
collections are written to continuously, and an offset page would duplicate and skip rows
under load.

A cursor names the **last row of the page**, and the predicate reads that row's sort values
back from the database. It deliberately does not carry the values themselves: doing so means
comparing a value bound from the client against one stored in the table, and the two can
disagree on precision — which shows up as a page that silently returns nothing.

If the row a cursor names has since been deleted the cursor cannot be resolved, and the
endpoint answers `422 stale_cursor` rather than an empty page: a page that silently drops the
rest of a collection is indistinguishable from reaching the end of it.

The sort must end in the row id, or the order is not total and a row can be served twice or
not at all. Each term carries its own direction (`column.asc()` / `column.desc()`), because an
ordering like "most severe first, then newest" mixes them — collapsing that to one direction
for the whole sort silently reverses a key.

**Bounded collections** — bounded by deployment topology or by an already-bounded parent —
return a bare array and are exempt. The exemption list is exhaustive:

`/workspaces` · `/workspaces/{workspace}/members` · `/catalogs` ·
`/workspaces/{workspace}/catalogs` · `/agents` · `/admin/agents` · `/admin/agents/metrics` ·
`/admin/storage-backends` · `/admin/users/{user_id}/workspaces` ·
`/admin/service-accounts/{service_account_id}/pats` · `/me/pats` ·
`/workspaces/{workspace}/schedules` ·
`/workspaces/{workspace}/semantic/models` ·
`/workspaces/{workspace}/semantic/models/{model}/metrics/{metric}/dimensions` ·
`/workspaces/{workspace}/catalogs/{catalog}/schemas`

Adding to this list requires an argument that the collection cannot grow without bound.

Two endpoints are exempt for a different reason, and both say so at the route:

- **A tail is not a page.** `/catalogs/{catalog_id}/migrations/{migration_id}/logs` takes
  `after`, the last sequence number the client saw, and is polled forward. The collection
  envelope's cursor goes null on the last page, which would lose exactly the position the
  client polls from.
- **A list the server already holds entire is not worth paging.**
  `/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables` and
  `/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}/snapshots` are
  proxied from Iceberg REST, which the Polaris client calls without a page token, so the whole
  identifier list arrives in one response regardless. Paging them would be a fiction until that
  client supports page tokens.

**`RowsPageOut` is not a collection envelope.** It describes tabular query output — `rows`,
`columns`, `column_schema`, `cursor`, `total` — and is deliberately a different type from a
resource collection. Do not reshape it to match.

**Search returns a report, not a page**: `{"items": [...], "has_more": …}` plus any
diagnostics, with no cursor. Search is truncated by `limit`, not walked — a cursor that never
advances would be a lie, and `has_more` is what tells a caller to narrow the query instead.

`limit` is `default=100, ge=1, le=1000` unless a per-endpoint cost justifies otherwise, and
the justification goes in the handler docstring.

## Filtering and sorting

`GET /workspaces/{workspace}/queries` is the canonical filter set. Any list endpoint
offering these concepts uses these names and types:

| Concept | Parameter | Type |
|---|---|---|
| State filter | `status` | repeated string, no default |
| Principal | `user_id`, `agent_id` | UUID |
| Time window | `since`, `until` | ISO-8601 datetime |
| Free text | `q` | string |
| Sort | `sort`, `dir` | enum; `dir` is `asc` or `desc` |
| Page | `cursor`, `limit` | opaque string, int |

`status` is **always** a repeated string with **no default**. A default filter belongs in
the client, not the contract — a caller who omits the parameter should get everything, not a
subset they did not ask for.

`q` is **required** where the endpoint is a search.

Filtering and sorting happen server-side over the whole result set, before the page is cut.
A client that sorted the page it was handed would be sorting a hundred rows out of
thousands.

## Errors

Every `4xx` and `5xx` response uses one envelope:

```json
{
  "error": "sql_not_allowed",
  "message": "DDL is not permitted in this session.",
  "details": {}
}
```

- **`error`** — stable machine-readable `snake_case` code. Branch on this, never on
  `message`.
- **`message`** — human-readable and safe to display.
- **`details`** — optional object carrying endpoint-specific structured context.

The envelope is produced centrally by an exception handler, so handlers keep raising
`HTTPException` as they always have. A `dict` detail carrying `error` maps straight through;
a plain string detail becomes a `message` with an `error` code derived from the status:

| Status | Derived code |
|---|---|
| 401 | `unauthorized` |
| 403 | `forbidden` |
| 404 | `not_found` |
| 409 | `conflict` |
| 422 | `unprocessable_content` |
| 5xx | `internal_error` |
| any other 4xx | `bad_request` |

Prefer raising a specific code over relying on the derived one.

The envelope covers **every** error, including ones no route declares: a 405 from
a wrong method, and an uncaught exception, both come back in it. An unhandled
exception is logged with its traceback and reported as `internal_error` with a
fixed message — a crash is exactly when a caller most needs a parseable body, and
exactly when it must not be handed a stack trace.

!!! info "Why not RFC 9457?"
    [Problem Details](https://www.rfc-editor.org/rfc/rfc9457) was evaluated and not adopted.
    Its `application/problem+json` media type trips content-type sniffing in several client
    generators, none of its field names match what consumers already read, and it earns its
    keep mainly through a maintained type-URI registry that DuckHaven has no consumer for.
    The deciding factor is what clients can consume without special-casing.

## OpenAPI metadata

1. Every operation sets an explicit `operation_id`: `snake_case`, `verb_subject`, globally
   unique, and **stable across releases** — it becomes a method name in generated clients.
2. Every operation has a `summary` (imperative fragment, 60 characters or fewer) and a
   `description` (the handler docstring).
3. Every operation declares the errors it can return. By construction, the minimum is:
    - `401` wherever `get_current_user` is a dependency
    - `403` wherever `require_permission`, `require_agent_tier`, or
      `assert_workspace_member` is
    - `404` on every path with an id segment
    - `409` and `503` wherever the handler raises them
4. One tag per operation, drawn from the allow-list, never duplicated. Tags become client
   class names, so a tag covering five unrelated resources produces an unusable class.
5. A deprecated operation carries `deprecated: true` and names its replacement in the
   description.
6. **Authentication is described once, as a security scheme — never as per-call
   parameters.** The schema declares `cookieAuth` and `bearerAuth` under
   `components.securitySchemes`, and every authenticated operation references one. The
   `session` cookie and `Authorization` header are `include_in_schema=False`, so a generated
   client configures credentials once rather than accepting them as arguments on every
   method. This describes the existing cookie-or-Bearer model; it does not change it.

## Versioning

`GET /api/version` returns the contract version:

```json
{ "version": "1.4.0", "api_version": 2 }
```

`api_version` is a single integer, bumped **only** when a change breaks the contract on the
wire — a changed URL, request body, response body, or status code. Schema-level changes do
not bump it: renaming a path *template* parameter, adding a declared response, splitting a
tag, or setting an `operation_id` leaves every request byte-identical. Bumping for those
would make the integer meaningless.

There is no `/v1/` or `/v2/` path prefix, and none is planned. A version segment would
double the routing surface and give a consumer no way to discover the version without
guessing a URL, which `/api/version` already answers in one unauthenticated request.

See the [REST API reference](rest-api.md) for the surface itself.
