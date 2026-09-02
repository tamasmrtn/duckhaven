# REST API

The DuckHaven control plane is a FastAPI application. The web UI is a thin client over the same REST API documented
here, so anything the UI does can be scripted.

## Base path and interactive docs

The REST API is mounted under **`/api`** (it shares an origin with the SPA). FastAPI serves live, interactive
documentation generated from the running server:

- Swagger UI: `http://<host>:8000/api/docs`
- OpenAPI schema: `http://<host>:8000/api/openapi.json`

Because the schema is generated from the server, it always matches the deployed version — prefer it over any static
list.

## Server version

`GET /api/version` is an unauthenticated endpoint that reports which build is running:

```json
{
  "version": "1.4.0",
  "api_version": 2
}
```

The two fields are distinct on purpose:

- **`version`** — the release/build version of the running server (the git tag it was built from). Use it for
  provenance and bug reports — *"which build is this?"*. It moves with every release.
- **`api_version`** — the API contract version, a single integer bumped only when a change breaks the contract. It does
  not move on ordinary releases.

A server old enough to lack this endpoint returns **404**; treat that as the oldest supported version.

## Resource groups

| Group | Covers |
|---|---|
| `auth` | Sign in / out, current user (`/api/auth/*`, `/api/me`) |
| `setup` | First-admin creation from the setup token |
| `workspaces` | Create and list workspaces and members |
| `catalog` | Catalogs (create/attach/detach/drop), [storage migrations](#catalog-storage-migrations), schemas, tables, table detail, sample rows, snapshot history |
| `queries` | Submit queries, page result rows, profiles, saved queries, history |
| `lineage` | Read a table's [lineage](../concepts/lineage.md) graph; import and retire external lineage |
| `semantic` | Define, validate, publish and query [semantic models](../concepts/semantic-layer.md) |
| `agents` | List the agents you may use, with their capabilities |
| `admin-agents` | Agent detail, monitoring, lifecycle, bootstrap/revoke, [access](#per-agent-access) |
| `admin-users` | User accounts and their workspace roles |
| `admin-service-accounts` | Service accounts and their tokens |
| `admin-storage` | Storage backends |
| `admin-maintenance` | Maintenance policy and scans |

## Authentication

The API accepts two credentials, both resolving to the same authorization checks:

- **Session cookie** — for browser clients. Authenticate as you would in the UI and send the session cookie with each
  request.
- **Bearer token** — for machine clients. Send a [service-account PAT](../guides/service-accounts.md) on the
  `Authorization: Bearer <token>` header. This is the supported path for unattended callers (CI, schedulers, tooling).

See [Permissions](../concepts/permissions.md) for the underlying model. Both are declared in the OpenAPI schema as
security schemes (`cookieAuth`, `bearerAuth`), so a generated client configures credentials once rather than passing
them on every call.

### Issuing your own token

A signed-in person can mint a token for themselves, which is how a human gets a credential for a command-line client
without an admin issuing one:

```sh
curl -X POST "$DH/api/me/pats" \
  -H 'Content-Type: application/json' \
  -b "session=$SESSION_COOKIE" \
  -d '{"expires_in_days": 90}'
```

```json
{ "id": "…", "token": "dh_pat_…", "expires_at": "2026-11-29T09:14:00Z" }
```

The token carries the caller's own identity, so it can do exactly what they can — and it tracks their role as it
changes, because permissions are resolved from the user on each request rather than frozen into the token. The secret
is returned once and only its SHA-256 hash is stored.

Two properties are deliberate:

- **`expires_in_days` is mandatory and capped at 365.** The admin-issued service-account form accepts `null` for a
  token that never expires, because an admin grants it knowingly to an unattended pipeline. A token anyone can mint
  for themselves is not that.
- **This route accepts the session cookie only.** Presenting a bearer token returns **403 `session_required`**. A
  token able to mint tokens would outlive its own revocation — revoking the leaked one would leave every successor it
  issued working — so issuing one always costs an interactive sign-in.

A principal may hold **25 live tokens**; issuing a 26th returns **409 `too_many_tokens`**. Self-issuance needs no
permission, so a ceiling is what keeps the collection bounded — revoke one you no longer use to make room.

Unattended callers do not use this endpoint. CI, schedulers and tooling authenticate with a
[service-account PAT](../guides/service-accounts.md), issued by an admin at
`POST /api/admin/service-accounts/{service_account_id}/pats`. A service account presenting its own token here gets
**403 `service_account_tokens_are_managed`**: its tokens are deliberately issued by an administrator at different
trust levels, and one of them must not be able to revoke another.

!!! note "Only password sign-ins can reach this today"
    Issuing requires a session cookie, and the only client that mints one is `dh auth login`, which signs in with a
    password. On a deployment that authenticates solely through an identity provider there is no way for a person to
    issue themselves a token — the SPA has no interface for it — so ask an administrator for a
    [service-account token](../guides/service-accounts.md) instead.

### Managing your own tokens

`GET /api/me/pats` lists what you hold, and `DELETE /api/me/pats/{pat_id}` revokes one:

```json
[
  { "id": "…", "created_at": "2026-08-31T09:00:00Z", "expires_at": "2026-11-29T09:00:00Z",
    "current": true }
]
```

**The listing never returns a token, and cannot.** Only a SHA-256 hash of the secret is stored, so a
token is shown once — when it is issued — and a forgotten one is replaced rather than recovered. This
is the same contract GitHub and GitLab publish for their access tokens.

That leaves a listing of hashes with nothing a person can read, so the token authenticating the
request is marked **`current`**. Without it a caller holding three tokens sees three
indistinguishable rows and cannot tell which expiry is the one about to break them.

Unlike issuing, both of these accept a bearer token as well as a session:

- **Listing** is what lets a client warn you before your own token expires, and a client
  authenticates with that very token.
- **Revoking** only ever removes access, so a leaked token cannot escalate with it — and a token
  able to retire itself is worth more than the nuisance of one being used to retire its siblings.
  GitLab reaches the same conclusion, letting any token call its self-revocation route.

Revoking another user's token returns **404**, not 403, so the endpoint cannot be used to discover
that one exists.

## Errors

Every `4xx` and `5xx` response has the same body:

```json
{
  "error": "sql_not_allowed",
  "message": "DDL is not permitted in this session.",
  "details": {}
}
```

- **`error`** — a stable machine-readable code. Branch on this, never on `message`.
- **`message`** — human-readable and safe to display.
- **`details`** — optional structured context; present only where an endpoint documents it (for example the list of
  dependents blocking a semantic dataset delete).

Codes are specific where the endpoint has something specific to say (`sql_not_allowed`, `agent_required`,
`catalog_read_only`, `invalid_cursor`) and derived from the status otherwise (`unauthorized`, `forbidden`, `not_found`,
`conflict`, `unprocessable_content`).

## Pagination

Collections that grow with usage return a page:

```json
{ "items": [], "cursor": null, "has_more": false }
```

Pass `cursor` to fetch the next page and `limit` to size it (default 100, max 1000). The cursor is opaque — feed back
exactly what you were given. It is keyset-based, not an offset, so a page stays correct while rows are being written
ahead of it. `has_more` tells you whether another page exists; `cursor` is `null` on the last one.

Collections bounded by your deployment's topology — workspaces, members, catalogs, agents, storage backends, schedules,
semantic models — return a plain array and take no cursor. Two endpoints are deliberately different:

- **Search** (`/search`, `/semantic/search`) returns `{"items": [...], "has_more": …}` with no cursor. Search is
  truncated by `limit`, not walked; narrow the query instead.
- **Migration logs** returns a plain array and takes `after`, the last sequence number you saw. It is a tail you poll
  forward, not a page you walk.

Query result rows (`/queries/{query_id}/rows`) use a different envelope again — `rows`, `columns`, `column_schema` —
because a result grid is not a resource collection.

!!! note "Private by design"
    DuckHaven has no public ingress; the API is reachable only on your private network (Tailscale recommended). The
    agent control channel is a separate WebSocket at `/agents/connect`, not part of this REST surface.

## Migrating to `api_version` 2

`api_version` moved from `1` to `2`. Check it at `GET /api/version` before assuming any of the below. Everything here
changes on the wire; nothing else about the API did.

### Every error body changed shape

Errors were `{"detail": "..."}`, or `{"detail": {"error": ..., "detail": ...}}` for the ones carrying a machine code.
Both are now the single [error envelope](#errors). Read `message` for display and `error` for branching; the codes that
existed under the old nested shape kept their names.

### Nine collections became pages

These returned a bare JSON array and now return `{"items": [...], "cursor": ..., "has_more": ...}` — see
[Pagination](#pagination). Read `.items` where you read the array before, and page with `cursor` if you need more than
the first 100.

`GET /api/admin/users` · `GET /api/admin/service-accounts` · `GET /api/maintenance/recommendations` ·
`GET /api/workspaces/{workspace}/saved-queries` · `GET /api/workspaces/{workspace}/sql/sessions` ·
`GET /api/sql/sessions/{session_id}/statements` · `GET /api/workspaces/{workspace}/schedule-runs` ·
`GET /api/workspaces/{workspace}/schedules/{schedule_id}/runs` ·
`GET /api/workspaces/{workspace}/assistant/conversations` · `GET /api/catalogs/{catalog_id}/migrations`

### Routes that moved

| Was | Is | Why |
|---|---|---|
| `POST /api/workspaces/{workspace}/catalogs/attach` with `{"catalog_id": …}` | `PUT /api/workspaces/{workspace}/catalogs/{catalog}` with `{"make_default": …}` | Attaching is a membership write, so it lives at the membership's own address — the one `DELETE` already used. Idempotent: `201` the first time, `200` after. The catalog is named by slug in the path. |
| `POST /api/admin/service-accounts/{id}/pat` | `POST /api/admin/service-accounts/{service_account_id}/pats` | The sub-resource was singular on two of its three routes. |
| `DELETE /api/admin/service-accounts/{id}/pat/{pat_id}` | `DELETE /api/admin/service-accounts/{service_account_id}/pats/{pat_id}` | As above. |
| `POST /api/workspaces/{workspace}/schemas/refresh-stats` | `POST /api/workspaces/{workspace}/catalogs/{catalog}/refresh-stats` | It walks every schema in the catalog, so it was never a schema-level operation — and under `/schemas/` it occupied the slot a namespace of that name would need. |

### The default-catalog shim is gone

Fourteen operations served schemas and tables without naming a catalog, resolving to the workspace's default. They were
duplicates of the catalog-scoped family and are removed. Add the catalog segment:

```text
/api/workspaces/{workspace}/schemas/...
→ /api/workspaces/{workspace}/catalogs/{catalog}/schemas/...
```

`GET /api/workspaces/{workspace}/catalogs` lists the attached catalogs and marks the default with `is_default`, if you
need to reproduce the old behaviour explicitly.

### Status codes that became honest

`POST /api/setup/admin`, `POST /api/admin/agents/bootstrap` and `POST /api/sql/sessions/{session_id}/staging-files`
return **201**; they create. Four more operations already returned a code they did not declare, so a client written
against the old schema may see one it was not expecting:

- `PUT .../catalogs/{catalog}/grants` and `PUT /api/admin/agents/{agent_id}/grants` return **201** on create, **200** on
  replace.
- `POST .../saved-queries` returns **200** when it overwrites a query of the same name, **201** when it creates one.
- `POST .../sql/sessions` returns **202** when `on_wait_timeout=continue` and compute is still starting.

### Search

Both search endpoints now return `{"items": [...], …}` — `GET .../semantic/search` renamed `hits` to `items` — and `q`
is required rather than defaulting to empty.

### Two new error codes to expect

- **`stale_cursor` (422)** on a paged collection, when the row your cursor names has been
  deleted. Start paging again from the beginning; it is not an error in your request so much
  as a position that no longer exists.
- **`bad_request` (4xx)** is now the code derived for any 4xx the API does not name
  specifically — a `405` from a wrong method, for instance. It previously read
  `internal_error`, which pointed at the wrong side of the connection.

An unhandled server error also returns the envelope now, as `internal_error`, rather than a
plain-text body.

### Filters no longer default

`GET /api/maintenance/recommendations` used to default to `status=open`. It now returns every state unless you ask:
send `?status=open` for the outstanding ones. `status` is repeatable everywhere it appears, including on
`GET /api/workspaces/{workspace}/semantic/models`, where it previously took a single value.

---

## Result column types

`GET /api/queries/{query_id}` and `GET /api/queries/{query_id}/rows` both return a **`column_schema`** field
describing the
result's columns:

```json
"column_schema": [
  { "name": "shipped_at", "type": "TIMESTAMP WITH TIME ZONE" },
  { "name": "amount", "type": "DECIMAL(38,10)" }
]
```

`type` is DuckDB's own logical-type spelling — the same string `DESCRIBE` prints, and the same one you can cast back to
(`SELECT NULL::DECIMAL(38,10)`). It is complete on its own: precision, scale and nested field types are inside the
string, so there are no separate `precision`/`scale` fields. See
[Column types](../concepts/query-execution.md#column-types) for how the types are captured and why.

The field is **additive** — `columns` still carries the names-only list it always has — and is `null` in two cases:

- the statement produced no result grid (DDL and DML), and
- the query ran on an agent older than this feature. The control plane reports nothing rather than deriving types from
  the result Parquet, whose writer is lossy.

## Query history

`GET /api/workspaces/{workspace}/queries` is the reference implementation of the
[collection page](#pagination), and carries the richest filter set on the API — the vocabulary every other filtered
list follows:

```json
{
  "items": [ /* QueryOut */ ],
  "cursor": "eyJ...",
  "has_more": true
}
```

Pass `cursor` back to fetch the next page; it is `null` on the last one. The cursor is opaque and is tied to the
`sort` it was produced under — reusing one after changing `sort` is a `422`, not a silently different page.

There is **no total**, here or on any paged collection. Counting the rows behind a filtered page means a second pass
over the same predicates on every request, for a number that is stale as soon as another query is submitted.
`has_more` costs nothing and is what the UI reports.

### Parameters

| Parameter | Type | Notes |
|---|---|---|
| `q` | string | Case-insensitive substring of the statement. `%` and `_` are matched literally |
| `query_id` | string | A full query id or its leading characters |
| `since`, `until` | ISO 8601 | Bound `started_at` |
| `status` | string, repeatable | `queued`, `running`, `done`, `failed`, `cancelled` |
| `statement_type` | string, repeatable | `select`, `insert`, `update`, `delete`, `merge`, `copy`, `create`, `alter`, `drop`, `describe`, `other` |
| `slower_than_ms` | integer | See duration below |
| `sort` | `started_at` \| `duration` | Default `started_at` |
| `dir` | `asc` \| `desc` | Default `desc` |
| `cursor` | string | From the previous page |
| `limit` | integer 1-1000 | Page size, default 100 |
| `origin`, `session_id`, `agent_id` | | Narrow to a kind of run, one session, or one agent |
| `user_id`, `all_workspaces` | | Cross-principal; see below |

An unrecognized value for an enumerated parameter is rejected with `422` rather than ignored. Sorting and filtering
are applied to the whole result set before the page is cut.

### Duration

`slower_than_ms` and `sort=duration` use the agent's execution time when it reported one, and otherwise
`finished_at - started_at`. Without that fallback every failed run would be excluded, which is backwards: a statement
that hung and then died is what a slow-query search is for. Runs that have not finished have no duration — they are
excluded from `slower_than_ms` and sort last under `sort=duration` in **both** directions.

### Statement type

Classified when the row is written. `null` means unknown — the statement did not parse, or it predates the field — and
is distinct from `other`, which means it parsed and nothing more specific fit. Rows with `null` are returned normally
and are excluded only when `statement_type` is supplied.

### Permissions

Any workspace member may use every filter above against their own workspace, including `since`/`until` and their own
`user_id`. A `user_id` other than the caller's own, and `all_workspaces`, require the query-admin permission and are
otherwise `403`.

Runs whose `origin` is `sample`, `metadata` or `maintenance` are never returned.

## Semantic layer

Define what business terms mean, and compile questions into SQL from those definitions. See
[Semantic layer](../concepts/semantic-layer.md).

| Method & path | Purpose |
|---|---|
| `GET /api/workspaces/{workspace}/semantic/models` | List models. Optional `status` filter. A model binding any table the caller cannot read is absent, not forbidden. |
| `POST /api/workspaces/{workspace}/semantic/models` | Create a model. Requires workspace **writer**. |
| `GET /api/workspaces/{workspace}/semantic/models/{model}` | One model with its datasets, dimensions, metrics and relationships. |
| `PATCH /api/workspaces/{workspace}/semantic/models/{model}` | Rename or re-describe. **409** on an imported model — a model has one owner. |
| `DELETE /api/workspaces/{workspace}/semantic/models/{model}` | Delete. Requires workspace **owner**. |
| `POST /api/workspaces/{workspace}/semantic/models/{model}/publish` | Make the model authoritative to the assistant. Validates first; **422** if anything is broken. Requires workspace **owner**. |
| `POST /api/workspaces/{workspace}/semantic/models/{model}/deprecate` | Retire it: still readable, excluded from new answers. Requires **owner**. |
| `POST /api/workspaces/{workspace}/semantic/models/{model}/validate` | Resolve every binding against the live catalog and record the outcome. |
| `POST /api/workspaces/{workspace}/semantic/models/{model}/{datasets,dimensions,metrics,relationships}` | Add a definition. Requires **writer**, plus `metadata` tier on any table a dataset binds. **409** if the name is already used in this model. |
| `PATCH /api/workspaces/{workspace}/semantic/models/{model}/metrics/{metric}` | Edit a metric. Resets its validation state to `unchecked`. |
| `DELETE /api/workspaces/{workspace}/semantic/models/{model}/{metrics,dimensions,relationships}/{metric,dimension,relationship}` | Remove one definition. Requires **writer**; **409** on an imported model. A dimension is refused with **409** while a metric is measured on it — an absent time axis is indistinguishable from one never set, and would be answered on the dataset's default date. |
| `DELETE /api/workspaces/{workspace}/semantic/models/{model}/datasets/{dataset}` | Remove a dataset. **409** naming the dependents while any dimension, metric or relationship still binds it — the delete would otherwise cascade to them. |
| `GET /api/workspaces/{workspace}/semantic/models/{model}/metrics/{metric}/dimensions` | The dimensions this metric can legally be sliced by. |
| `GET /api/workspaces/{workspace}/semantic/search?q=` | Rank metrics and dimensions against a question. Returns `items`, an `ambiguous` list of equally-matching metrics, and a `broken` list of matching definitions that exist but no longer resolve. |
| `POST /api/workspaces/{workspace}/semantic/compile` | Compile a metric request to SQL. **Does not execute** — submit the SQL through `POST /api/workspaces/{workspace}/queries` like any other statement. |
| `POST /api/workspaces/{workspace}/semantic/imports/{provider}` | Publish definitions from a producer, as `text/plain`. `duckhaven` takes a YAML document; `dbt` takes a `manifest.json`. `?reconcile=provider_run` (default) retires models the payload no longer declares. Requires **writer**. |
| `DELETE /api/workspaces/{workspace}/semantic/imports?provider=<name>` | Remove everything a provider published. Requires workspace **owner**. |
| `GET /api/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}/semantic` | Which definitions depend on this table. Optional `column` narrows it. Requires `metadata` tier. |

`POST /api/workspaces/{workspace}/semantic/compile` takes structured input only — metric and dimension names, an
operator from a fixed set, values
as JSON, and a time window as a kind plus a count. There is no field into which SQL can be passed. Refusals come back
as **422** naming the legal alternatives: an unknown metric lists the real ones, an ambiguous join path names both
candidates, and a grain a dimension does not support lists the ones it does.

Time windows must be stated explicitly: `last_complete` (the last N complete periods), `trailing` (a rolling window
ending today), `to_date` (period start through today), or `absolute` (explicit dates, end exclusive). There is no
default, because "last month" means a different window to different people.

An artifact the chosen provider cannot read — YAML posted to `dbt`, an empty file, anything that is not a JSON object
where a manifest belongs — comes back as **422** naming the format that provider expects, not a 500. Which matters to a
pipeline: a 5xx reads as "retry me", and a wrong file will never import no matter how often it is sent.

## Lineage

Read the [lineage](../concepts/lineage.md) graph around a table, and import lineage produced elsewhere.

| Method & path | Purpose |
|---|---|
| `GET /api/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}/lineage` | The bounded graph around a table. Requires `metadata` tier on the table. |
| `POST /api/workspaces/{workspace}/lineage/imports` | Import canonical edges from any producer. Requires workspace **writer**, plus `writer` on each target's catalog. |
| `POST /api/workspaces/{workspace}/lineage/imports/{provider}` | Import a producer's own artifact — `dbt` takes a `manifest.json` body, or `{"manifest": …, "catalog": …}` to include column detail. Same authorization. |
| `DELETE /api/workspaces/{workspace}/lineage/imports?provider=<name>` | Remove every edge a retired producer asserted. Requires workspace **owner**. |

Read parameters: `direction` (`upstream` \| `downstream` \| `both`, default `both`), `depth` (1–5, default 2), a
repeatable `provider` filter, and a repeatable `columns_for` taking node keys. The response carries `nodes`, `edges`,
`truncated`, `hidden` and `columns_truncated`. `truncated` is `true` when a cap stopped the walk early; `hidden` is
`true` when the walk reached lineage in a catalog the workspace does not attach and dropped it — deliberately a bare
flag, so a caller can tell "nothing here" from "something here you may not see" without learning anything about what
was withheld.

Node `kind` is `table`, `external` (an asset outside DuckHaven, named by whoever imported it), or `redacted` (a table
in a scoped catalog the caller holds no grant on — present with no names, so the graph keeps its shape). Each node
also carries `column_count`: how many of its columns take part in the lineage around it, and so how many rows it would
show if opened. It arrives for every node — unlike the mappings, which only arrive for the nodes `columns_for` names —
because it is what lets a client decide whether a node is worth opening. It is `0` for a redacted node, and `0`
whenever there is nothing to show. Every edge
carries a `providers` list — one entry per producer, each with its own `first_seen_at`, `last_seen_at`,
`observation_count`, `stale` and `column_lineage` — plus edge-level totals and a `stale` that is `true` only when every
producer's claim is stale.

### Column detail

`columns` is populated only for edges touching a node named in `columns_for`, and is empty otherwise. Column detail
scales with how wide the tables are rather than how many nodes the walk found, so it is fetched for the nodes a caller
is actually looking at instead of for the whole graph. `columns_truncated` is `true` when a cap stopped it short; the
graph's own shape is still complete when it is.

Each entry has `source_column`, `target_column`, the `providers` asserting it, and `stale`. Column detail is withheld
entirely when either endpoint is `redacted`.

`column_lineage` says how to read an empty `columns`, and the three values are different answers:

| Value | Meaning |
|---|---|
| `derived` | Worked out. With no columns listed, this means none of the source's values reach the target — it was joined against or filtered on. |
| `unsupported` | Something tried and could not establish it. |
| `unknown` | Nothing tried. |

On import, `LineageEdgeIn` accepts a `columns` list of `{source_column, target_column}` and an optional
`column_lineage`. Omitted, it is inferred: `derived` when columns were sent, `unknown` otherwise. Sending `derived`
with an empty list is how a producer states that it checked and nothing flows. Any other value is rejected with
**422**.

The provider name `execution` is reserved for lineage DuckHaven derives from SQL it ran: importing it is rejected with
**422**, and it cannot be purged. Imports are idempotent, and edges whose endpoints cannot be resolved are returned in
`skipped` alongside a **200** rather than failing the whole batch.

## Catalog storage migrations

Move a catalog to a different [storage backend](../concepts/storage-backends.md). All endpoints require the catalog's
creator or an admin with the catalogs permission. See
[Migrate a catalog's storage](../guides/migrate-catalog-storage.md).

| Method & path | Purpose |
|---|---|
| `POST /api/catalogs/{catalog_id}/migrations` | Start a migration. Body: `{"target_storage_backend_id": "<uuid>"}`. Returns **202** with the migration record (`pending`). |
| `GET /api/catalogs/{catalog_id}/migrations` | List the catalog's migrations, newest first. |
| `GET /api/catalogs/{catalog_id}/migrations/{migration_id}` | Status and progress (phase, table counts, bytes, per-table state). |
| `GET /api/catalogs/{catalog_id}/migrations/{migration_id}/logs` | User-facing log stream. `?after=<seq>` returns only events newer than `seq` (incremental polling). |
| `POST /api/catalogs/{catalog_id}/migrations/{migration_id}/cancel` | Request cancellation (only before cutover). |

While a migration is active the catalog is read-only: write queries against a workspace with the migrating catalog
attached are rejected with **409** (`{"error": "catalog_read_only"}`); reads are unaffected.

## Per-agent access

Every agent endpoint below `/api/admin/agents/{agent_id}` is authorized by the caller's **tier** on that specific
agent — `use` < `operate` < `admin` — rather than by the global `agents:manage` permission alone. Holding
`agents:manage` confers `admin` on every agent. See
[Per-agent access](../concepts/permissions.md#per-agent-access) for the model.

| Tier required | Endpoints |
|---|---|
| `use` | `GET /api/admin/agents/{agent_id}`, `GET /api/admin/agents/{agent_id}/monitoring` |
| `operate` | `POST …/{id}/restart`, `POST …/{id}/terminate`, `POST …/{id}/disconnect`, `DELETE …/{id}/credential` |
| `admin` | `DELETE /api/admin/agents/{agent_id}`, and the access endpoints below |

`POST /api/admin/agents/elastic`, `POST /api/admin/agents/bootstrap` and `GET /api/admin/agents/compute-options`
remain on the global `agents:manage` permission: they are fleet-level, not about one agent. `POST .../elastic` accepts
an optional `access_mode` (`open` | `restricted`, default `open`) so a reserved agent is created locked down rather
than narrowed afterwards — it is applied to the row before the compute backend is asked for anything.

An agent the caller has no tier on is **invisible** — omitted from `GET /api/agents`, `GET /api/admin/agents` and
`GET /api/admin/agents/metrics`, and **404** from its own routes. An insufficient-but-nonzero tier returns **403**
with `{"error": "agent_forbidden"}`. The same `use` check applies wherever an agent is named for work: submitting a
query or opening a SQL session with an explicit `agent_id`, and setting `agent_id` on a schedule or
`default_agent_id` on a saved query.

Every agent object carries `access_tier` (the requesting caller's tier) and `access_mode` (`open` | `restricted`).

| Method & path | Purpose |
|---|---|
| `GET /api/admin/agents/{agent_id}/access` | The agent's access mode, its grants, and the candidate principals to grant to. |
| `PATCH /api/admin/agents/{agent_id}/access-mode` | Body: `{"access_mode": "open" \| "restricted"}`. Returns the full access payload. |
| `PUT /api/admin/agents/{agent_id}/grants` | Upsert a grant. Body: `{"user_id"` **or** `"workspace_id", "tier"}`. **201** on insert, **200** on update. **422** if neither or both principals are given, or if a workspace is granted `admin`. |
| `DELETE /api/admin/agents/{agent_id}/grants/{grant_id}` | Revoke a grant. **204**. |

## Waiting for compute

When [elastic compute](../concepts/elastic-compute.md) is enabled, a request can arrive with the pool
scaled to zero. Submitting a **query** is unaffected: it is already asynchronous, so the run is
recorded `queued` with a null `agent_id` and dispatches once compute registers — you poll
`GET /api/queries/{query_id}` exactly as you would for a busy agent. That now also covers a query naming an
idle-terminated elastic agent, which used to be **503**.

Opening a **SQL session** is synchronous, so it cannot simply park. `POST /api/workspaces/{workspace}/sql/sessions`
accepts two optional fields for it:

| Field | Default | Meaning |
|---|---|---|
| `wait_timeout_s` | server default (`SQL_SESSION_WAIT_TIMEOUT_S`, 45s) | How long to block while compute starts. `0` never blocks. Above `SQL_SESSION_MAX_WAIT_TIMEOUT_S` is **422**. |
| `on_wait_timeout` | `cancel` | `cancel` → **503** with `Retry-After` and `{"error": "compute_starting"}`; `continue` → **202** with the session still `pending`. `0` with `cancel` is **422**. |

A **202** means the session exists but is not usable yet: poll `GET /api/sql/sessions/{session_id}` until its
status reads `open` (a statement sent before then is **409** `session_not_open`). A **503** here means
the compute is still coming up, not that it failed — retry, and the retry lands on the agent that is
already starting.

`API_VERSION` is deliberately **unchanged**: both fields are optional, an older server ignores them,
and a client that never sends them sees exactly the previous 201/503 behaviour. A **202** on this
route is itself the signal that a server supports the contract, so nothing needs to negotiate.
