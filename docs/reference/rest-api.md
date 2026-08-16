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
  "api_version": 1
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

## Lineage

Read the [lineage](../concepts/lineage.md) graph around a table, and import lineage produced elsewhere.

| Method & path | Purpose |
|---|---|
| `GET /api/workspaces/{ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/lineage` | The bounded graph around a table. Requires `metadata` tier on the table. |
| `POST /api/workspaces/{ws}/lineage/imports` | Import canonical edges from any producer. Requires workspace **writer**, plus `writer` on each target's catalog. |
| `POST /api/workspaces/{ws}/lineage/imports/{provider}` | Import a producer's own artifact — `dbt` takes a `manifest.json` body. Same authorization. |
| `DELETE /api/workspaces/{ws}/lineage/imports?provider=<name>` | Remove every edge a retired producer asserted. Requires workspace **owner**. |

Read parameters: `direction` (`upstream` \| `downstream` \| `both`, default `both`), `depth` (1–5, default 2), and a
repeatable `provider` filter. The response carries `nodes`, `edges`, `truncated` and `hidden`. `truncated` is `true`
when a cap stopped the walk early; `hidden` is `true` when the walk reached lineage in a catalog the workspace does not
attach and dropped it — deliberately a bare flag, so a caller can tell "nothing here" from "something here you may not
see" without learning anything about what was withheld.

Node `kind` is `table`, `external` (an asset outside DuckHaven, named by whoever imported it), or `redacted` (a table
in a scoped catalog the caller holds no grant on — present with no names, so the graph keeps its shape). Every edge
carries a `providers` list — one entry per producer, each with its own `first_seen_at`, `last_seen_at`,
`observation_count` and `stale` — plus edge-level totals and a `stale` that is `true` only when every producer's claim
is stale. The `columns` list is always empty today.

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
