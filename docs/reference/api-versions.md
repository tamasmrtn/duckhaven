---
type: reference
---

# API versions

`GET /api/version` reports `api_version`, an integer that changes only when a change breaks existing API clients.

This page lists each version, what changed on the wire, and which client releases read it. Read it before you upgrade
a server that scripts, dbt, dlt or BI tools talk to. Features that only add optional fields keep the version; they are
listed under [Additions that keep the version](#additions-that-keep-the-version).

## Check the version

The endpoint needs no credentials:

```bash
curl -s "$DH/api/version"
```

```json
{"version": "0.0.0-dev", "api_version": 3}
```

| Field | Meaning |
|---|---|
| `version` | The release the server was built from, such as `0.8.0`. A local build reports `0.0.0-dev`. Quote it in bug reports. |
| `api_version` | The contract version. A client checks this to decide how to read responses. |

A server that answers **404** predates the endpoint (v0.4.0 and older). Treat it as older than `api_version` 1.

## What changes the version

`api_version` changes when a request or response that worked before stops working the same way: a URL moves, a request
or response body changes shape, or a status code changes. New endpoints and new optional fields do not change it, and
neither do schema-only changes such as a renamed path parameter or a new `operation_id`, which leave every request
byte-identical.

There is no `/v1/` path prefix. One server serves one version, and `GET /api/version` is how a client finds out which.

## Versions

| `api_version` | Servers |
|---|---|
| 3 | Builds from `main` after v0.8.0, including the `:latest` image |
| 2 | v0.8.0 |
| 1 | v0.5.0 – v0.7.0 |

### `api_version` 3: exact numbers are strings

Result values that a JSON number cannot hold exactly arrive as strings. A JSON number is a 64-bit float in most
readers, every browser included: it holds integers exactly only up to 2^53, and no decimal fraction exactly.

| Column type | `api_version` 2 | `api_version` 3 |
|---|---|---|
| `DECIMAL(p,s)` | `12.5` (a float) | `"12.50"` (every digit, scale kept) |
| `BIGINT`, `UBIGINT` | `9007199254740992` (rounded past 2^53) | `"9007199254740993"` |
| `HUGEINT`, `UHUGEINT` | a float | `"12345678901234567890123"` |

The rule follows the column's type, not the value, everywhere rows appear: `GET /api/queries/{query_id}/rows`, a SQL
statement's `first_page`, and table samples. It applies inside lists, structs and maps too. `count(*)` and `sum()` over
integers return `BIGINT` or `HUGEINT`, so their results are strings. See
[Result values](rest-api.md#result-values) for how to read them.

**Upgrade the clients first.** `duckhaven-sql-connector` 0.8.0 reads versions 2 and 3 the same way, so upgrade it
before the server. `dbt-duckhaven` and `dlt-duckhaven` read results through it:

```bash
pip install --upgrade "duckhaven-sql-connector>=0.8.0"
```

`dh` 0.4.0 prints these values the same way in table and CSV output. With `--format json` they stay strings, so scripts
that read `dh` JSON must convert them. Your own code should convert each value by its column's type in `column_schema`:
integers to a type that holds 64 bits or more (`BigInt` in JavaScript), decimals to a decimal type.

### `api_version` 2: one error shape, paged collections, moved routes

**Errors.** Every error body became the [error envelope](errors.md), `{"error", "message", "details"}`. Version 1 sent
`{"detail": "…"}`, or `{"detail": {"error": …, "detail": …}}` where it carried a code. Codes kept their names.

**Paged collections.** These returned a bare array and return a [page](rest-api.md#pagination),
`{"items": […], "cursor": …, "has_more": …}`:

- `GET /api/admin/users` and `GET /api/admin/service-accounts`
- `GET /api/maintenance/recommendations`
- `GET /api/workspaces/{workspace}/saved-queries`
- `GET /api/workspaces/{workspace}/sql/sessions` and `GET /api/sql/sessions/{session_id}/statements`
- `GET /api/workspaces/{workspace}/schedule-runs` and `GET /api/workspaces/{workspace}/schedules/{schedule_id}/runs`
- `GET /api/workspaces/{workspace}/assistant/conversations`
- `GET /api/catalogs/{catalog_id}/migrations`

**Moved routes.**

| Version 1 | Version 2 |
|---|---|
| `POST /api/workspaces/{workspace}/catalogs/attach` with `{"catalog_id": …}` | `PUT /api/workspaces/{workspace}/catalogs/{catalog}` with `{"make_default": …}`: **201** the first time, **200** after |
| `POST /api/admin/service-accounts/{id}/pat` | `POST /api/admin/service-accounts/{service_account_id}/pats` |
| `DELETE /api/admin/service-accounts/{id}/pat/{pat_id}` | `DELETE /api/admin/service-accounts/{service_account_id}/pats/{pat_id}` |
| `POST /api/workspaces/{workspace}/schemas/refresh-stats` | `POST /api/workspaces/{workspace}/catalogs/{catalog}/refresh-stats` |
| `/api/workspaces/{workspace}/schemas/…` (the default catalog) | `/api/workspaces/{workspace}/catalogs/{catalog}/schemas/…` |

For the last row, `GET /api/workspaces/{workspace}/catalogs` marks the default catalog with `is_default`.

**Status codes.**

- `POST /api/setup/admin`, `POST /api/admin/agents/bootstrap` and `POST /api/sql/sessions/{session_id}/staging-files`
  return **201**.
- The two grant upserts return **201** on create and **200** on replace.
- `POST …/saved-queries` returns **200** when it replaces a query of the same name.
- `POST …/sql/sessions` returns **202** with `on_wait_timeout=continue` while compute starts.
- `POST …/statements` returns **200** when the statement finished within its wait.

**Search and filters.**

- Both search endpoints return `items`; `GET …/semantic/search` called it `hits`. `q` is required.
- `GET /api/maintenance/recommendations` returns every state unless you send `status`; version 1 defaulted to `open`.
- `status` is repeatable everywhere, including on semantic models.

**New error codes.** `stale_cursor` (**422**) when the row a cursor names was deleted, and `bad_request` for any 4xx
the API does not name, such as a **405**, which version 1 reported as `internal_error`.

## Additions that keep the version

These request fields are optional. A server without them ignores the field, so a client can send it to any server:

| Field | On | Effect |
|---|---|---|
| `wait_timeout_s` | `POST …/queries`, `GET /api/queries/{query_id}` | Hold the response until the query finishes |
| `wait_timeout_s`, `on_wait_timeout` | `POST …/sql/sessions` | [Wait for compute](sql-sessions-api.md#waiting-for-compute) |
| `wait_timeout_s`, `first_page_limit`, `inline_format` | `POST …/statements` | [One-call statements](sql-sessions-api.md#waiting-for-a-statement) |
| `parameters` | `POST …/queries`, `POST …/statements` | [Placeholders](rest-api.md#query-parameters). A server without it fails the statement for missing values |
| `tags` | `POST …/queries`, `POST …/statements` | [Labels on the run](rest-api.md#client-and-tags) |
| `use_cache` | `POST …/queries`, sessions, statements | Skip the [result cache](rest-api.md#result-cache-fields) |

A **202** from `POST …/sql/sessions`, or a **200** from `POST …/queries`, is itself the sign that the server supports
the matching field.

## Related

- [REST API](rest-api.md) — the conventions and endpoints.
- [Errors](errors.md) — every error code.
- [Update DuckHaven](../deployment/updating.md) — upgrading the server.
