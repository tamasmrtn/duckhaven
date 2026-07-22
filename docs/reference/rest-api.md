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
| `agents` | List agents and capabilities |
| `admin` | Agents (bootstrap/revoke), storage backends, users, service accounts & PATs, maintenance |

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
