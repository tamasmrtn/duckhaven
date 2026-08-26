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
| `admin` | Agents (detail, monitoring, lifecycle, bootstrap/revoke, [access](#per-agent-access)), storage backends, users, service accounts & PATs, maintenance |

## Authentication

The API accepts two credentials, both resolving to the same authorization checks:

- **Session cookie** — for browser clients. Authenticate as you would in the UI and send the session cookie with each
  request.
- **Bearer token** — for machine clients. Send a [service-account PAT](../guides/service-accounts.md) on the
  `Authorization: Bearer <token>` header. This is the supported path for unattended callers (CI, schedulers, tooling).

See [Permissions](../concepts/permissions.md) for the underlying model.

!!! note "Private by design"
    DuckHaven has no public ingress; the API is reachable only on your private network (Tailscale recommended). The
    agent control channel is a separate WebSocket at `/agents/connect`, not part of this REST surface.

## Result column types

`GET /api/queries/{id}` and `GET /api/queries/{id}/rows` both return a **`column_schema`** field describing the
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

`GET /api/workspaces/{ws}/queries` returns a page, not a bare array:

```json
{
  "items": [ /* QueryOut */ ],
  "cursor": "eyJ...",
  "has_more": true
}
```

Pass `cursor` back to fetch the next page; it is `null` on the last one. The cursor is opaque and is tied to the
`sort` it was produced under — reusing one after changing `sort` is a `422`, not a silently different page.

There is **no total**. Counting the rows behind a filtered page means a second pass over the same predicates on every
request, for a number that is stale as soon as another query is submitted. `has_more` costs nothing and is what the
UI reports.

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
| `GET /api/workspaces/{ws}/semantic/models` | List models. Optional `status` filter. A model binding any table the caller cannot read is absent, not forbidden. |
| `POST /api/workspaces/{ws}/semantic/models` | Create a model. Requires workspace **writer**. |
| `GET /api/workspaces/{ws}/semantic/models/{slug}` | One model with its datasets, dimensions, metrics and relationships. |
| `PATCH /api/workspaces/{ws}/semantic/models/{slug}` | Rename or re-describe. **409** on an imported model — a model has one owner. |
| `DELETE /api/workspaces/{ws}/semantic/models/{slug}` | Delete. Requires workspace **owner**. |
| `POST /api/workspaces/{ws}/semantic/models/{slug}/publish` | Make the model authoritative to the assistant. Validates first; **422** if anything is broken. Requires workspace **owner**. |
| `POST /api/workspaces/{ws}/semantic/models/{slug}/deprecate` | Retire it: still readable, excluded from new answers. Requires **owner**. |
| `POST /api/workspaces/{ws}/semantic/models/{slug}/validate` | Resolve every binding against the live catalog and record the outcome. |
| `POST /api/workspaces/{ws}/semantic/models/{slug}/{datasets,dimensions,metrics,relationships}` | Add a definition. Requires **writer**, plus `metadata` tier on any table a dataset binds. **409** if the name is already used in this model. |
| `PATCH /api/workspaces/{ws}/semantic/models/{slug}/metrics/{name}` | Edit a metric. Resets its validation state to `unchecked`. |
| `DELETE /api/workspaces/{ws}/semantic/models/{slug}/{metrics,dimensions,relationships}/{name}` | Remove one definition. Requires **writer**; **409** on an imported model. A dimension is refused with **409** while a metric is measured on it — an absent time axis is indistinguishable from one never set, and would be answered on the dataset's default date. |
| `DELETE /api/workspaces/{ws}/semantic/models/{slug}/datasets/{name}` | Remove a dataset. **409** naming the dependents while any dimension, metric or relationship still binds it — the delete would otherwise cascade to them. |
| `GET /api/workspaces/{ws}/semantic/models/{slug}/metrics/{name}/dimensions` | The dimensions this metric can legally be sliced by. |
| `GET /api/workspaces/{ws}/semantic/search?q=` | Rank metrics and dimensions against a question. Returns `hits`, an `ambiguous` list of equally-matching metrics, and a `broken` list of matching definitions that exist but no longer resolve. |
| `POST /api/workspaces/{ws}/semantic/compile` | Compile a metric request to SQL. **Does not execute** — submit the SQL through `POST /queries` like any other statement. |
| `POST /api/workspaces/{ws}/semantic/imports/{provider}` | Publish definitions from a producer, as `text/plain`. `duckhaven` takes a YAML document; `dbt` takes a `manifest.json`. `?reconcile=provider_run` (default) retires models the payload no longer declares. Requires **writer**. |
| `DELETE /api/workspaces/{ws}/semantic/imports?provider=<name>` | Remove everything a provider published. Requires workspace **owner**. |
| `GET /api/workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/semantic` | Which definitions depend on this table. Optional `column` narrows it. Requires `metadata` tier. |

`POST /semantic/compile` takes structured input only — metric and dimension names, an operator from a fixed set, values
as JSON, and a time window as a kind plus a count. There is no field into which SQL can be passed. Refusals come back
as **422** naming the legal alternatives: an unknown metric lists the real ones, an ambiguous join path names both
candidates, and a grain a dimension does not support lists the ones it does.

Time windows must be stated explicitly: `last_complete` (the last N complete periods), `trailing` (a rolling window
ending today), `to_date` (period start through today), or `absolute` (explicit dates, end exclusive). There is no
default, because "last month" means a different window to different people.

## Lineage

Read the [lineage](../concepts/lineage.md) graph around a table, and import lineage produced elsewhere.

| Method & path | Purpose |
|---|---|
| `GET /api/workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/lineage` | The bounded graph around a table. Requires `metadata` tier on the table. |
| `POST /api/workspaces/{ws}/lineage/imports` | Import canonical edges from any producer. Requires workspace **writer**, plus `writer` on each target's catalog. |
| `POST /api/workspaces/{ws}/lineage/imports/{provider}` | Import a producer's own artifact — `dbt` takes a `manifest.json` body, or `{"manifest": …, "catalog": …}` to include column detail. Same authorization. |
| `DELETE /api/workspaces/{ws}/lineage/imports?provider=<name>` | Remove every edge a retired producer asserted. Requires workspace **owner**. |

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
| `GET /api/catalogs/{catalog_id}/migrations/{id}` | Status and progress (phase, table counts, bytes, per-table state). |
| `GET /api/catalogs/{catalog_id}/migrations/{id}/logs` | User-facing log stream. `?after=<seq>` returns only events newer than `seq` (incremental polling). |
| `POST /api/catalogs/{catalog_id}/migrations/{id}/cancel` | Request cancellation (only before cutover). |

While a migration is active the catalog is read-only: write queries against a workspace with the migrating catalog
attached are rejected with **409** (`{"error": "catalog_read_only"}`); reads are unaffected.

## Per-agent access

Every agent endpoint below `/api/admin/agents/{agent_id}` is authorized by the caller's **tier** on that specific
agent — `use` < `operate` < `admin` — rather than by the global `agents:manage` permission alone. Holding
`agents:manage` confers `admin` on every agent. See
[Per-agent access](../concepts/permissions.md#per-agent-access) for the model.

| Tier required | Endpoints |
|---|---|
| `use` | `GET /api/admin/agents/{id}`, `GET /api/admin/agents/{id}/monitoring` |
| `operate` | `POST …/{id}/restart`, `POST …/{id}/terminate`, `POST …/{id}/disconnect`, `DELETE …/{id}/credential` |
| `admin` | `DELETE /api/admin/agents/{id}`, and the access endpoints below |

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
| `GET /api/admin/agents/{id}/access` | The agent's access mode, its grants, and the candidate principals to grant to. |
| `PATCH /api/admin/agents/{id}/access-mode` | Body: `{"access_mode": "open" \| "restricted"}`. Returns the full access payload. |
| `PUT /api/admin/agents/{id}/grants` | Upsert a grant. Body: `{"user_id"` **or** `"workspace_id", "tier"}`. **201** on insert, **200** on update. **422** if neither or both principals are given, or if a workspace is granted `admin`. |
| `DELETE /api/admin/agents/{id}/grants/{grant_id}` | Revoke a grant. **204**. |

## Waiting for compute

When [elastic compute](../concepts/elastic-compute.md) is enabled, a request can arrive with the pool
scaled to zero. Submitting a **query** is unaffected: it is already asynchronous, so the run is
recorded `queued` with a null `agent_id` and dispatches once compute registers — you poll
`GET /api/queries/{id}` exactly as you would for a busy agent. That now also covers a query naming an
idle-terminated elastic agent, which used to be **503**.

Opening a **SQL session** is synchronous, so it cannot simply park. `POST /api/workspaces/{ws}/sql/sessions`
accepts two optional fields for it:

| Field | Default | Meaning |
|---|---|---|
| `wait_timeout_s` | server default (`SQL_SESSION_WAIT_TIMEOUT_S`, 45s) | How long to block while compute starts. `0` never blocks. Above `SQL_SESSION_MAX_WAIT_TIMEOUT_S` is **422**. |
| `on_wait_timeout` | `cancel` | `cancel` → **503** with `Retry-After` and `{"error": "compute_starting"}`; `continue` → **202** with the session still `pending`. `0` with `cancel` is **422**. |

A **202** means the session exists but is not usable yet: poll `GET /api/sql/sessions/{id}` until its
status reads `open` (a statement sent before then is **409** `session_not_open`). A **503** here means
the compute is still coming up, not that it failed — retry, and the retry lands on the agent that is
already starting.

`API_VERSION` is deliberately **unchanged**: both fields are optional, an older server ignores them,
and a client that never sends them sees exactly the previous 201/503 behaviour. A **202** on this
route is itself the signal that a server supports the contract, so nothing needs to negotiate.
