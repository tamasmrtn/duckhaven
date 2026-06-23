# System catalog reference

The built-in [system catalog](../concepts/system-catalog.md) is addressed as
`duckhaven` in SQL and is read-only. This page lists its v1 tables and columns.

Latency: `query`/`access` tables are refreshed on the materializer interval
(`SYSTEM_CATALOG_SYNC_INTERVAL_S`, default 60s); `info_schema` is a snapshot
rebuilt each cycle. All rows are visible to every workspace member.

## `duckhaven.query.history`

One row per **finished** query (status `done`, `failed`, or `cancelled`).
Incrementally appended from the Postgres query log; Postgres is the source of
truth.

| Column | Type | Notes |
|---|---|---|
| `query_id` | string | the query's UUID |
| `workspace_id` / `workspace_slug` | string | originating workspace |
| `agent_id` / `agent_name` | string | executing agent (null if none) |
| `user_id` / `user_email` | string | submitting user (null for internal queries) |
| `statement_type` | string | `SELECT` / `INSERT` / `CREATE` / … (parsed) |
| `status` | string | `done` / `failed` / `cancelled` |
| `origin` | string | null for user queries; `sample` / `metadata` / `maintenance` for internal |
| `row_count` | long | result or affected rows |
| `result_bytes` | long | size of the materialized result |
| `duration_ms` | long | execution time |
| `reserved_memory_bytes` | long | admission reservation (from the profile, best-effort) |
| `reserved_threads` | int | admission reservation (from the profile, best-effort) |
| `error` | string | failure message, if any |
| `started_at` / `finished_at` | timestamp (UTC) | lifecycle timestamps |

## `duckhaven.access.audit`

One event per finished query — a projection of `query.history` into an
event shape. Records **query activity**; not yet permission/membership changes.

| Column | Type | Notes |
|---|---|---|
| `event_time` | timestamp (UTC) | `finished_at`, else `started_at` |
| `query_id` | string | the related query |
| `actor` | string | `user_email` of the submitter |
| `action` | string | the statement type |
| `workspace_slug` | string | workspace the action occurred in |
| `status` | string | outcome |

## `duckhaven.info_schema.*`

A current-state snapshot of objects **across all catalogs**, rebuilt each cycle
from Apache Polaris plus DuckHaven's metadata sidecar.

### `catalogs`

| Column | Type | Notes |
|---|---|---|
| `catalog` | string | catalog slug |
| `polaris_name` | string | Polaris warehouse name |
| `storage_kind` | string | `object_store` / `s3` / `adls_gen2` |
| `is_system` | bool | true for the system catalog itself |
| `created_at` | timestamp (UTC) | |

### `schemas`

| Column | Type |
|---|---|
| `catalog` | string |
| `schema_name` | string |

### `tables`

| Column | Type | Notes |
|---|---|---|
| `catalog` | string | |
| `schema_name` | string | |
| `table_name` | string | |
| `owner_email` | string | from the metadata sidecar (best-effort) |
| `row_count` | long | last known (best-effort) |
| `size_bytes` | long | last known (best-effort) |
| `last_write_at` | timestamp (UTC) | last write provenance |

### `columns`

| Column | Type | Notes |
|---|---|---|
| `catalog` | string | |
| `schema_name` | string | |
| `table_name` | string | |
| `column_name` | string | |
| `data_type` | string | Iceberg type text |
| `ordinal` | int | column position |

## Related

- [System catalog](../concepts/system-catalog.md) — concepts and scope.
- [Configuration](configuration.md) — `SYSTEM_CATALOG_SYNC_*` settings.
