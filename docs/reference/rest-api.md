---
type: reference
---

# REST API

DuckHaven's REST API, under `/api`, does everything the web UI does, so any task can be scripted with a token.

The running server publishes its own complete, always-current description: Swagger UI at `/api/docs` and the OpenAPI
schema at `/api/openapi.json`. This page covers what the schema cannot say: the conventions every endpoint follows,
and the endpoints whose behavior needs explaining. Error codes are listed in [Errors](errors.md), and the contract's
version history in [API versions](api-versions.md).

## A first call

You need a personal access token (`dh_pat_…`): a [service-account token](../access/service-accounts.md) for automation,
or your own from `dh auth login`. Submit a query and wait up to 30 seconds for it to finish:

```bash
export DH=https://duckhaven.example.com
export DH_TOKEN=dh_pat_…

curl -s -X POST "$DH/api/workspaces/analytics/queries" \
  -H "Authorization: Bearer $DH_TOKEN" -H "Content-Type: application/json" \
  -d '{
        "sql": "SELECT customer, sum(amount) AS revenue FROM sales.analytics.orders GROUP BY 1 ORDER BY 2 DESC",
        "wait_timeout_s": 30
      }'
```

The answer is **200** with the finished query (some fields omitted):

```json
{
  "id": "360e83e5-4c9f-4c32-b903-f52b021f2932",
  "status": "done",
  "statement_type": "select",
  "row_count": 3,
  "duration_ms": 76,
  "column_schema": [
    {"name": "customer", "type": "VARCHAR"},
    {"name": "revenue", "type": "DECIMAL(38,2)"}
  ],
  "has_result": true,
  "error": null
}
```

Then read its rows:

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" "$DH/api/queries/360e83e5-4c9f-4c32-b903-f52b021f2932/rows"
```

```json
{
  "rows": [
    {"customer": "initech", "revenue": "310.00"},
    {"customer": "acme", "revenue": "162.25"},
    {"customer": "globex", "revenue": "75.50"}
  ],
  "columns": ["customer", "revenue"],
  "column_schema": [{"name": "customer", "type": "VARCHAR"}, {"name": "revenue", "type": "DECIMAL(38,2)"}],
  "cursor": null,
  "total": 3
}
```

`revenue` is a string because a JSON number cannot hold every decimal exactly; see [Result values](#result-values). A
query that runs longer than the wait answers **202** instead; see [Submitting a query](#submitting-a-query).

## Conventions

### Authentication

Apart from a few public endpoints, such as `GET /api/version` and sign-in, every endpoint needs a credential:

- **Bearer token**: `Authorization: Bearer dh_pat_…`. Use this from scripts and tools. DuckHaven reads tokens only from
  this header, never from a query parameter or cookie, because those end up in proxy and access logs.
- **Session cookie**: what the browser sends after signing in.

Both resolve to the same account and the same [access checks](../access/how-access-works.md). The OpenAPI schema
declares them as the `bearerAuth` and `cookieAuth` security schemes, so a generated client sets credentials once.

### Addresses

- The API is mounted at `/api`, on the same origin as the web UI.
- `{workspace}` accepts the workspace's slug (`analytics`) or its UUID.
- Objects with a server-generated id are created and listed under their workspace, and read or changed at a global
  address: `POST /api/workspaces/{workspace}/queries` creates a query, and `GET /api/queries/{query_id}` reads it. An
  id is enough to reach the object; access is still checked.
- `{schema}` and `{table}` are the names the catalog holds, not slugs.

### Methods and status codes

| Method | Meaning |
|---|---|
| `GET` | Reads; no side effects |
| `POST` | Creates, starts an action (such as `/cancel`), or opens a stream |
| `PUT` | Creates or replaces, idempotently |
| `PATCH` | Changes the fields you send. A field you leave out is untouched; an explicit `null` clears it |
| `DELETE` | Removes, answering **204**. A batch delete answers **200** with a count |

**201** answers a create with the new object. **202** means work was accepted and is still running: poll the object's
address. **204** means success with no body. Everything else that succeeds is **200**.

### Errors

Every failure has one body shape, `{"error": …, "message": …, "details": …}`. Branch on `error`, which is stable, and
show `message` to people. Every code is in [Errors](errors.md).

### Pagination

Collections that grow with use, such as query history, users and saved queries, return a page:

```json
{"items": [], "cursor": null, "has_more": false}
```

- Pass `cursor` back to get the next page, and `limit` to size it (default 100, at most 1000).
- `cursor` is opaque: send it back exactly as you got it. It is `null` on the last page.
- Pages stay correct while rows are being added, because a cursor names a position, not an offset.
- There is no total count. `has_more` says whether another page exists.
- A cursor whose row was deleted answers **422** `stale_cursor`: start again from the first page.

Collections bounded by the deployment's size return a plain array and take no cursor: workspaces, members, catalogs,
agents, storage backends, schedules and semantic models, among others. The OpenAPI schema shows which shape each
endpoint returns. Three endpoints differ:

- **Search** returns `{"items": […], "has_more": …}` with no cursor. Narrow the query instead of paging.
- **Migration logs** take `after`, the last sequence number you saw, and are polled forward.
- **Query rows** use their own shape, `rows`, `columns`, `column_schema`, `cursor` and `total`, described under
  [Reading results](#reading-results).

### Filtering and sorting

List endpoints share parameter names. Filtering and sorting cover the whole collection, before the page is cut.

| Parameter | Type | Meaning |
|---|---|---|
| `status` | repeatable string | Keep only these states. Omitted, every state is returned |
| `user_id`, `agent_id` | UUID | Keep only one principal's or agent's objects |
| `since`, `until` | ISO 8601 date-time | A time window |
| `q` | string | Free text; required on search endpoints |
| `sort`, `dir` | enum; `asc` or `desc` | Sort order |
| `cursor`, `limit` | string, integer | Paging |

An unrecognized value for an enumerated parameter is a **422**, not ignored.

### Versions

`GET /api/version` needs no credentials and reports the server's release and its contract version:

```json
{"version": "0.0.0-dev", "api_version": 3}
```

`version` is the release the server was built from (`0.0.0-dev` for a local build). `api_version` changes only when a
change breaks existing clients. What each version changed is in
[API versions](api-versions.md).

### Client and tags

DuckHaven records which tool sent a query, as `client_name` and `client_version`, from the first product token of the
request's `User-Agent`: `dbt-duckhaven/1.2.0 (linux)` records `dbt-duckhaven` and `1.2.0`, cut to 64 and 32 characters.
Start your `User-Agent` with your tool's name to be recognized. Both fields are `null` for a browser, and for runs
DuckHaven starts itself, such as schedules. A SQL session statement takes its session's client.

`POST /api/workspaces/{workspace}/queries` and `POST /api/sql/sessions/{session_id}/statements` also take `tags`, an
object of free-form labels returned on the run:

```json
{"sql": "SELECT 1", "tags": {"pipeline": "nightly", "run_id": "2026-10-19"}}
```

At most 20 tags. Keys are letters, digits and `_ . : -`, up to 64 characters; values are up to 256 characters. Anything
else is a **422**. Tags are stored as sent and not checked further, so treat them as the caller's claim.

## Endpoint groups

The OpenAPI schema groups endpoints by tag:

| Tag | Covers |
|---|---|
| `auth` | Sign in and out, the current user (`/api/auth/*`, `/api/me`), your own tokens |
| `setup` | Creating the first admin from the setup token |
| `workspaces` | Workspaces and their members |
| `catalog` | Catalogs, [storage migrations](#catalog-storage-migrations), schemas, tables, samples, snapshots |
| `queries` | Queries, results, profiles, saved queries, history |
| `worksheets` | Your private [worksheets](#worksheets) |
| `search` | Finding catalogs, schemas, tables and saved queries by name |
| `lineage` | A table's [lineage](#lineage), and importing lineage |
| `semantic` | [Semantic models](#semantic-layer) |
| `agents` | The agents you may use |
| `admin-agents` | Agent detail, monitoring, lifecycle and [access](#per-agent-access) |
| `admin-users` | User accounts and their workspace roles |
| `admin-service-accounts` | Service accounts and their tokens |
| `admin-storage` | Storage backends |
| `admin-maintenance` | Maintenance policy and scans |

The agents' own control channel is a WebSocket at `/agents/connect`, not part of this API.

## Your own tokens

A signed-in person can issue themselves a token, for a command-line client, without an admin. `dh auth login` does this
for you. The call needs the session cookie:

```bash
curl -s -X POST "$DH/api/me/pats" \
  -H "Content-Type: application/json" -b "session=$SESSION_COOKIE" \
  -d '{"expires_in_days": 90}'
```

```json
{"id": "…", "token": "dh_pat_…", "expires_at": "2027-01-17T09:14:00Z"}
```

- The token acts as you, with your role as it is at each request, so a role change applies to it at once.
- The secret is shown once; DuckHaven stores only its SHA-256 hash.
- `expires_in_days` is required and at most 365. Only an admin-issued service-account token may never expire.
- A bearer token cannot issue tokens: the call answers **403** `session_required`. A token that could mint more tokens
  would outlive its own revocation.
- You may hold 25 live tokens; a 26th answers **409** `too_many_tokens`.
- A service account calling this gets **403** `service_account_tokens_are_managed`: an admin issues its tokens.

Issuing needs a password sign-in, because `dh auth login` is the only client that creates the session cookie for it. On
a deployment that signs in only through an identity provider, ask an admin for a
[service-account token](../access/service-accounts.md).

### Managing your own tokens

`GET /api/me/pats` lists your tokens, and `DELETE /api/me/pats/{pat_id}` revokes one. Both accept a bearer token as
well as a session, so a client can warn you before its own token expires, and a token can revoke itself.

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" "$DH/api/me/pats"
```

```json
[{"id": "27ea2ee1-926c-4847-a602-736e9d9f1632", "created_at": "2026-10-19T14:23:12Z",
  "expires_at": "2027-01-17T14:23:12Z", "current": true}]
```

The list never includes the secret, which is not stored. `current` marks the token that made the request, so you can
tell which expiry applies to you. Revoking another user's token answers **404**, so the endpoint does not reveal that
it exists.

## Queries

### Submitting a query

`POST /api/workspaces/{workspace}/queries` runs one statement. The body takes:

| Field | Default | Meaning |
|---|---|---|
| `sql` | required | One statement. See [SQL support](sql-support.md) for what is allowed |
| `catalog` | the workspace's default | The catalog that unqualified table names resolve in |
| `agent_id` | chosen by the server | Run on this agent; you need the `use` tier on it |
| `timeout_s` | `600` | Cancel the query after this many seconds |
| `wait_timeout_s` | not sent | Hold the response until the query finishes, up to this many seconds (at most 60) |
| `parameters` | – | Values for placeholders; see [Query parameters](#query-parameters) |
| `use_cache` | `true` | `false` always runs the query instead of using the [result cache](#result-cache-fields) |
| `tags` | – | Labels; see [Client and tags](#client-and-tags) |

Without `wait_timeout_s` the call answers **202** at once with the query, `queued` or `running`. Poll
`GET /api/queries/{query_id}` until `status` is `done`, `failed` or `cancelled`. That `GET` also takes
`wait_timeout_s` as a query parameter, so each poll can wait for the end. With `wait_timeout_s`, the call answers
**200** with the finished query if it ends in time, and **202** otherwise. A query that outlasts the wait keeps running.

`has_result` says whether a finished query has rows to read: `false` for DDL and DML, `null` until the query is done.

Naming an agent that is offline, and that the API cannot start, answers **503** `unavailable` with "Agent not
connected". The attempt is recorded as a failed query, so it appears in history; its id is in `details.query_id`.

With [elastic compute](../compute/elastic-compute.md) and no agent running, the query is recorded `queued` with a
`null` `agent_id`, and runs once compute starts. You poll it as usual.

### Query parameters

`POST /api/workspaces/{workspace}/queries` and `POST /api/sql/sessions/{session_id}/statements` take `parameters`:
values for the placeholders in `sql`, as a list or an object. See [Query parameters](sql-support.md#query-parameters)
for the placeholder styles and typed values. Values that do not fit the placeholders answer **422**
`invalid_parameters`.

### Describing a query

`POST /api/workspaces/{workspace}/queries/describe` returns a query's result columns and types without running it,
which a driver or BI tool needs before executing. The agent binds the statement but reads no rows, so you need only the
`metadata` tier on the tables it names.

```bash
curl -s -X POST "$DH/api/workspaces/analytics/queries/describe" \
  -H "Authorization: Bearer $DH_TOKEN" -H "Content-Type: application/json" \
  -d '{"sql": "SELECT customer, sum(amount) AS revenue FROM sales.analytics.orders WHERE amount > ? GROUP BY 1"}'
```

```json
{
  "statement_type": "select",
  "columns": [{"name": "customer", "type": "VARCHAR"}, {"name": "revenue", "type": "DECIMAL(38,2)"}],
  "parameter_names": ["1"]
}
```

- `parameters` is optional. A placeholder without a value is typed as `NULL`, which DuckDB types as `INTEGER` when
  nothing else decides it, so send values when you have them. `parameter_names` lists the placeholders.
- A statement with no result grid, such as `INSERT` or `CREATE`, answers `columns: null` without using an agent.
- Several statements, a `DESCRIBE`, `SHOW`, `SUMMARIZE` or `PRAGMA`, a `PIVOT` without an `IN` list, and a statement
  that does not bind answer **422** `not_describable`. Describing a `PIVOT` needs `reader`, because its columns come
  from the data.
- With no agent running and elastic compute on, the call starts compute and waits up to `wait_timeout_s` (default
  `SQL_SESSION_WAIT_TIMEOUT_S`, 45 seconds). If compute is not up by then it answers **503** `compute_starting` with
  `Retry-After`; the retry lands on the agent that is starting.
- Describes do not appear in history and record no lineage.

### Reading results

| Endpoint | Returns |
|---|---|
| `GET /api/queries/{query_id}/rows` | One page of rows as JSON. `limit` (default 100, at most 1000) and `cursor` |
| `GET /api/queries/{query_id}/result` | The whole result as Arrow or Parquet; see [below](#whole-results-as-arrow-or-parquet) |
| `GET /api/queries/{query_id}/profile` | The query's [profile](../querying/query-profiles.md) |

`/rows` and `/result` answer **409** `Query not done` while the query runs, and **410** `gone` once the result has
been deleted after its retention period. All three answer **403** `grant_denied` when your current grants would not
let you run the query. In a workspace with a
[scoped catalog](../access/scoped-access.md#opening-a-result-needs-the-same-grants-as-running-its-query), opening a
result is checked like running it, for everyone including the person who ran it. In a workspace without one, any
member can read any result.

`column_schema`, on the query and on each page, gives each column's type as DuckDB spells it, the same string `DESCRIBE`
prints and that you can cast to (`DECIMAL(38,10)`). Precision, scale and nested field types are inside the string. It is
`null` for a statement with no result grid. See [Column types](../querying/query-execution.md#column-types).

#### Result values

A JSON number is a 64-bit float in most readers, so it holds integers exactly only up to 2^53 and no decimal fraction
exactly. Values of these column types are therefore sent as strings:

| Column type | Example value |
|---|---|
| `DECIMAL(p,s)` | `"12.50"`: every digit, scale kept |
| `BIGINT`, `UBIGINT` | `"9007199254740993"` |
| `HUGEINT`, `UHUGEINT` | `"12345678901234567890123"` |

The rule follows the column's type, not the value, so a column always has one JSON shape. It applies inside lists,
structs and maps, in `first_page`, and in table samples. `count(*)` and `sum()` over integers return `BIGINT` or
`HUGEINT`, so their results are strings too. Convert each value by its column's type in `column_schema`: integers to a
type of 64 bits or more (`BigInt` in JavaScript), decimals to a decimal type. Or read the result as Arrow, which keeps
every type.

### Whole results as Arrow or Parquet

`GET /api/queries/{query_id}/result` returns a finished query's entire result in one response, in a columnar format:

| `format` | Media type | What you get |
|---|---|---|
| `arrow` (default) | `application/vnd.apache.arrow.stream` | An [Arrow IPC stream](https://arrow.apache.org/docs/format/Columnar.html#ipc-streaming-format) |
| `parquet` | `application/vnd.apache.parquet` | The result file the agent wrote, byte for byte |

Use it for anything larger than a page or two, and wherever exact values matter: every value keeps its type, with no
strings to convert. In Python:

```python
import httpx
import pyarrow as pa

resp = httpx.get(
    f"{base}/api/queries/{query_id}/result", headers={"Authorization": f"Bearer {token}"}
)
table = pa.ipc.open_stream(resp.content).read_all()
```

- It answers like `/rows`, and **204** for a statement with no result grid.
- Errors are decided before the body starts, so a response that begins with **200** is a whole result.
- `HUGEINT` and `UHUGEINT` columns arrive as `DECIMAL(38,0)`, because Parquet has no 128-bit integer. `column_schema`
  still names the real type.
- An `INTERVAL` column arrives in Arrow as Parquet's raw 12-byte interval (months, days, milliseconds), because no Arrow
  type maps it without loss.

### Result cache fields

A query can be answered from the [result cache](../querying/result-cache.md) instead of running. `use_cache: false`
opts out on a query, a session statement, or a whole session when sent to `POST …/sql/sessions`. A workspace owner turns
the cache off for a workspace with `result_cache_enabled` on `PATCH /api/workspaces/{workspace}`.

| Field | Meaning |
|---|---|
| `cache_status` | `hit`: answered from an earlier run, nothing executed. `miss`: executed and offered to the cache. `bypass`: the cache was off or could not decide in time. `ineligible`: the query can never be cached. `null`: the cache did not look |
| `cache_detail` | The reason, when there is one: a slug such as `volatile_function`, `opted_out` or `changed_during_run` |
| `result_source_query_id` | For a hit, the run whose result was served. `/rows` and `/profile` of the hit answer with that run's rows and profile |

A hit is created already `done` and has a `null` `agent_id`, because no agent ran it.

### Query history

`GET /api/workspaces/{workspace}/queries` lists runs, newest first, as a [page](#pagination). It has the fullest filter
set in the API:

| Parameter | Type | Meaning |
|---|---|---|
| `q` | string | Case-insensitive substring of the SQL. `%` and `_` match literally |
| `query_id` | string | A full query id, or its first characters |
| `since`, `until` | ISO 8601 | Bounds on `started_at` |
| `status` | repeatable | `queued`, `running`, `done`, `failed`, `cancelled` |
| `statement_type` | repeatable | `select`, `insert`, `update`, `delete`, `merge`, `copy`, `create`, `alter`, `drop`, `describe`, `other` |
| `slower_than_ms` | integer | Runs that took longer than this |
| `sort` | `started_at` or `duration` | Default `started_at` |
| `dir` | `asc` or `desc` | Default `desc` |
| `origin`, `session_id`, `agent_id` | | A kind of run, one SQL session, or one agent |
| `user_id`, `all_workspaces` | | Another person's runs, or every workspace's |
| `cursor`, `limit` | | Paging; `limit` 1–1000, default 100 |

- **Duration** is the agent's execution time when it reported one, and otherwise `finished_at - started_at`, so failed
  runs count. Unfinished runs have none: `slower_than_ms` leaves them out, and `sort=duration` puts them last in both
  directions.
- **`statement_type`** `null` means the statement did not parse; `other` means it parsed and nothing more specific fit.
  Rows with `null` are left out only when you filter by `statement_type`.
- **A cursor belongs to its sort.** Reusing one after changing `sort` is a **422**.
- **Permissions.** Any member can use every filter on their own workspace, including their own `user_id`. Another
  person's `user_id`, and `all_workspaces`, need the `queries:admin` permission and are otherwise a **403**.
- Runs that DuckHaven makes for samples, metadata and maintenance are never listed.

## SQL sessions

A [SQL session](../querying/sql-sessions.md) holds a connection on one agent between statements, for dbt, dlt and other
drivers. Its endpoints, the waits for compute and for each statement, the first page of rows, Arrow results and staged
files are in the [SQL sessions API](sql-sessions-api.md).

## Workspace metadata

`GET /api/workspaces/{workspace}/metadata` returns everything you may see in a workspace as one tree: catalogs with
their kind and access mode, schemas, tables, and with `columns=true`, each table's columns. Drivers and BI tools answer
their catalog listings from it.

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" "$DH/api/workspaces/analytics/metadata?catalog=sales&columns=true"
```

```json
{
  "catalogs": [{
    "name": "sales", "kind": "iceberg_polaris", "access_mode": "open",
    "schemas": [{"name": "analytics", "tables": [{
      "name": "orders", "table_type": "MANAGED",
      "columns": [
        {"name": "order_id", "type": "INTEGER", "nullable": true},
        {"name": "customer", "type": "VARCHAR", "nullable": true},
        {"name": "amount", "type": "DECIMAL(10,2)", "nullable": true},
        {"name": "ordered_on", "type": "DATE", "nullable": true}
      ]
    }]}]
  }]
}
```

- **Filtered by grant.** In a scoped catalog you see only what you hold a tier on, as in the catalog tree.
- **Narrowed by `catalog`, `schema` and `table`.** An unknown `catalog` is a **404**. A catalog whose listing fails is
  left out instead of failing the report.
- **Names need no compute.** They come from each catalog's metastore.
- **Columns come from the engine.** With `columns=true` an agent runs `DESCRIBE` on each table, so types are in
  DuckDB's spelling (`BIGINT`, not Iceberg's `long`). A table that cannot be described, such as one dropped since it
  was listed, has `columns: null`.
- **Columns are capped** at `METADATA_COLUMNS_MAX_TABLES` tables (1000) per request; more is a **422**
  `too_many_tables`.
- **Columns may start compute.** With no agent running and elastic compute on, the call starts compute and waits, and
  answers **503** `compute_starting` if it is not up in time, as [describing a query](#describing-a-query) does.

## Worksheets

A worksheet is one editor tab: a private draft of its owner. Another member's worksheet answers **404**, like one that
does not exist. Every workspace member, readers included, can keep worksheets.

| Method and path | Does |
|---|---|
| `GET /api/workspaces/{workspace}/worksheets` | Your worksheets, as a [page](#pagination) |
| `POST /api/workspaces/{workspace}/worksheets` | Create one (**201**). An open worksheet becomes the last tab |
| `GET /api/workspaces/{workspace}/worksheets/{worksheet_id}` | One worksheet, open or closed |
| `PATCH /api/workspaces/{workspace}/worksheets/{worksheet_id}` | Save content, or change other fields |
| `DELETE /api/workspaces/{workspace}/worksheets/{worksheet_id}` | Delete it (**204**). A linked saved query is kept |

The list takes `status` (`open` or `closed`, repeatable), `saved_query_id`, `q` (a case-insensitive title substring),
`sort` (`updated_at`, `title` or `position`) and `dir`. `updated_at` sorts newest first.

### Saving content without losing a concurrent edit

`sql` and `title` are versioned. Send the `version` you last read as `base_version`:

```json
{"sql": "SELECT 1", "base_version": 4}
```

If the worksheet is still at that version, the content is saved and `version` goes up by one. If another window saved
it first, the answer is **409** `worksheet_conflict` with the current worksheet in `details.current`, so a client can
offer to load it or to save over it. Content without `base_version` is a **422** `base_version_required`.

The other fields, `agent_id`, `catalog`, `timeout_s`, `saved_query_id`, `last_query_id`, `is_open` and `tab_position`,
are last-write-wins and need no version; changing them moves neither `version` nor `updated_at`. `saved_query_id` and
`last_query_id` must name a saved query or query in the same workspace, and `agent_id` an agent you may use.

## Saved queries

`POST /api/workspaces/{workspace}/saved-queries` saves SQL under a name. Names are unique per workspace, ignoring case.
`on_conflict` decides what happens when the name is taken:

- `replace` (the default) replaces that query's SQL and default agent and answers **200**. A new name answers **201**.
- `error` answers **409** `saved_query_exists`, with the existing query's `id` and `name` in `details`, so a client can
  ask before overwriting a query others rely on.

`PATCH …/saved-queries/{saved_query_id}` edits or renames one; renaming onto a taken name is also a **409**.

Saved queries carry `created_by`, `updated_by` and `updated_at`. Changing the SQL or the default agent makes you the
`updated_by`, and a [scheduled run](../querying/schedule-queries.md#whose-data-grants-a-run-uses) uses
`updated_by`'s data grants.

## Search endpoint

`GET /api/workspaces/{workspace}/search?q=` finds catalogs, schemas, tables and saved queries whose names contain `q`,
ignoring case, filtered by your grants. It returns `{"items": […], "has_more": …}`. `limit` (default 20, at most 200)
caps it, and there is no cursor: narrow the query instead.

`types` (repeatable: `catalog`, `schema`, `table`, `saved_query`) limits the kinds returned and skips the work the
others need: asking for catalogs and schemas only never lists tables.

## Semantic layer

Define what business terms mean, and compile questions into SQL from those definitions. See
[Semantic layer](../metadata/semantic-layer.md).

| Method and path | Purpose |
|---|---|
| `GET …/semantic/models` | List models. Optional `status`. A model that binds a table you cannot read is left out |
| `POST …/semantic/models` | Create a model. Workspace `writer` |
| `GET …/semantic/models/{model}` | One model with its datasets, dimensions, metrics and relationships |
| `PATCH …/semantic/models/{model}` | Rename or re-describe. **409** `imported_model` on an imported model |
| `DELETE …/semantic/models/{model}` | Delete. Workspace `owner` |
| `POST …/semantic/models/{model}/publish` | Make it authoritative to the assistant. Validates first; **422** if anything is broken. `owner` |
| `POST …/semantic/models/{model}/deprecate` | Retire it: still readable, left out of new answers. `owner` |
| `POST …/semantic/models/{model}/validate` | Resolve every binding against the live catalog and record the outcome |
| `POST …/semantic/models/{model}/{datasets,dimensions,metrics,relationships}` | Add a definition. `writer`, plus the `metadata` tier on any table a dataset binds. **409** if the name is taken |
| `PATCH …/semantic/models/{model}/metrics/{metric}` | Edit a metric. Its validation state returns to `unchecked` |
| `DELETE …/semantic/models/{model}/{metrics,dimensions,relationships}/{name}` | Remove one definition. `writer`. A dimension a metric is measured on answers **409** `dimension_in_use` |
| `DELETE …/semantic/models/{model}/datasets/{dataset}` | Remove a dataset. **409** `dataset_in_use`, naming the dependents, while anything binds it |
| `GET …/semantic/models/{model}/metrics/{metric}/dimensions` | The dimensions this metric can be sliced by |
| `GET …/semantic/search?q=` | Rank metrics and dimensions against a question. Returns `items`, `ambiguous` (equally good metrics) and `broken` (matches whose bindings fail to resolve) |
| `POST …/semantic/compile` | Compile a metric request to SQL. Does not run it: submit the SQL as a query |
| `POST …/semantic/imports/{provider}` | Publish definitions from a producer, as `text/plain`: a YAML document for `duckhaven`, a `manifest.json` for `dbt`. `?reconcile=provider_run` (the default) retires models missing from the payload. `writer` |
| `DELETE …/semantic/imports?provider=⟨name⟩` | Remove everything a provider published. `owner` |
| `GET …/catalogs/{catalog}/schemas/{schema}/tables/{table}/semantic` | The definitions that depend on this table. Optional `column`. `metadata` tier |

`…` stands for `/api/workspaces/{workspace}`.

`POST …/semantic/compile` takes structured input only: metric and dimension names, operators from a fixed set, values
as JSON, and a time window. No field accepts SQL. A refusal is a **422** that names the legal alternatives: the real
metrics for an unknown one, both candidates for an ambiguous join path, the supported grains for a dimension.

Time windows are stated explicitly, because "last month" means different windows to different people: `last_complete`
(the last N complete periods), `trailing` (a rolling window ending today), `to_date` (period start through today) or
`absolute` (explicit dates, end exclusive).

A file the provider cannot read, such as YAML sent to `dbt` or an empty body, answers **422** naming the format that
provider expects. It is not a 5xx, because resending the same file will never work.

## Lineage

Read the [lineage](../metadata/lineage.md) graph around a table, and import lineage produced elsewhere.

| Method and path | Purpose |
|---|---|
| `GET …/catalogs/{catalog}/schemas/{schema}/tables/{table}/lineage` | The graph around a table. `metadata` tier on the table |
| `POST …/lineage/imports` | Import edges from any producer. Workspace `writer`, plus `writer` on each target's catalog |
| `POST …/lineage/imports/{provider}` | Import a producer's own file: `dbt` takes a `manifest.json`, or `{"manifest": …, "catalog": …}` for column detail. Same access |
| `DELETE …/lineage/imports?provider=⟨name⟩` | Remove every edge a producer asserted. Workspace `owner` |

`…` stands for `/api/workspaces/{workspace}`.

The read takes `direction` (`upstream`, `downstream` or `both`, the default), `depth` (1–5, default 2), a repeatable
`provider` filter, and a repeatable `columns_for` of node keys. The response has `nodes`, `edges`, `truncated`, `hidden`
and `columns_truncated`:

- `truncated` is `true` when a limit stopped the walk early.
- `hidden` is `true` when the walk reached a catalog the workspace does not attach and dropped it. It is a bare flag,
  so you can tell "nothing here" from "something you may not see" without learning what was withheld.

A node's `kind` is `table`, `external` (an asset outside DuckHaven, named by whoever imported it) or `redacted` (a table
in a scoped catalog you hold no grant on, shown without names so the graph keeps its shape). Every node has
`column_count`, the number of its columns that take part in the lineage around it, so a client can decide whether a
node is worth opening; it is `0` for a redacted node.

Each edge has a `providers` list, one entry per producer with its own `first_seen_at`, `last_seen_at`,
`observation_count`, `stale` and `column_lineage`, plus edge totals and a `stale` that is `true` only when every
producer's claim is stale.

### Column detail

`columns` is filled only for edges that touch a node named in `columns_for`. Column detail grows with the tables'
width, not the number of nodes, so a client fetches it for the nodes it shows. `columns_truncated` is `true` when a
limit cut it short; the graph's shape is still complete. Each entry has `source_column`, `target_column`, the
`providers` that assert it and `stale`. Column detail is withheld when either end is `redacted`.

`column_lineage` says how to read an empty `columns`:

| Value | Meaning |
|---|---|
| `derived` | Worked out. With no columns listed, no value of the source reaches the target: it was joined or filtered on |
| `unsupported` | Something tried and could not establish it |
| `unknown` | Nothing tried |

On import, an edge takes a `columns` list of `{source_column, target_column}` and an optional `column_lineage`. Left
out, it is `derived` when columns were sent and `unknown` otherwise. Send `derived` with an empty list to state that
you checked and nothing flows; any other value is a **422**.

The provider name `execution` is reserved for lineage DuckHaven derives from SQL it ran: importing under it is a
**422**, and it cannot be removed. Imports are idempotent. Edges whose ends cannot be resolved are returned in
`skipped`, with a **200**, instead of failing the batch.

## Catalog storage migrations

Move a catalog to another [storage backend](../data/storage-backends.md). Every endpoint needs the catalog's creator,
or an admin with `catalogs:admin`. See [Migrate a catalog's storage](../data/migrate-catalog-storage.md).

| Method and path | Purpose |
|---|---|
| `POST /api/catalogs/{catalog_id}/migrations` | Start one. Body: `{"target_storage_backend_id": "⟨uuid⟩"}`. **202** with the migration, `pending` |
| `GET /api/catalogs/{catalog_id}/migrations` | The catalog's migrations, newest first |
| `GET /api/catalogs/{catalog_id}/migrations/{migration_id}` | Status and progress: phase, table counts, bytes, per-table state |
| `GET /api/catalogs/{catalog_id}/migrations/{migration_id}/logs` | The log. `?after=⟨seq⟩` returns only newer events, for polling |
| `POST /api/catalogs/{catalog_id}/migrations/{migration_id}/cancel` | Cancel, before cutover only |

While a migration runs, the catalog is read-only: writes in any workspace that attaches it answer **409**
`catalog_read_only`. Reads work.

## Agents

### Per-agent access

Every endpoint under `/api/admin/agents/{agent_id}` checks your [tier](../access/agent-access.md) on that agent, `use`,
`operate` or `admin`. The `agents:manage` permission gives `admin` on every agent.

| Tier | Endpoints |
|---|---|
| `use` | `GET …/{agent_id}`, `GET …/{agent_id}/monitoring`, and `GET …/{agent_id}/queries`, which also needs `queries:admin` |
| `operate` | `POST …/{agent_id}/restart`, `/terminate` and `/disconnect`, and `DELETE …/{agent_id}/credential` |
| `admin` | `DELETE …/{agent_id}`, and the access endpoints below |

`POST /api/admin/agents/elastic`, `POST /api/admin/agents/bootstrap` and `GET /api/admin/agents/compute-options` need
the `agents:manage` permission, because they concern the fleet, not one agent. `POST …/elastic` takes an optional
`access_mode` (`open`, the default, or `restricted`), applied before the agent is provisioned.

An agent you hold no tier on is invisible: it is left out of `GET /api/agents`, `GET /api/admin/agents` and
`GET /api/admin/agents/metrics`, and its own routes answer **404**. A tier that is too low answers **403**
`agent_forbidden`. The `use` check also applies wherever you name an agent for work: a query or SQL session with
`agent_id`, a schedule's `agent_id`, or a saved query's `default_agent_id`.

Every agent object has `access_tier`, your tier on it, and `access_mode`, `open` or `restricted`.

| Method and path | Purpose |
|---|---|
| `GET /api/admin/agents/{agent_id}/access` | The access mode, the grants, and the people and workspaces you could grant to |
| `PATCH /api/admin/agents/{agent_id}/access-mode` | Body `{"access_mode": "open"}` or `"restricted"`. Returns the full access payload |
| `PUT /api/admin/agents/{agent_id}/grants` | Add or change a grant. Body: a `user_id` or a `workspace_id`, and a `tier`. **201** on create, **200** on change. **422** for neither or both principals, or `admin` for a workspace |
| `DELETE /api/admin/agents/{agent_id}/grants/{grant_id}` | Revoke a grant. **204** |

### Agent monitoring

The two endpoints behind an agent's [Monitoring tab](../compute/agent-monitoring.md):

| Method and path | Purpose |
|---|---|
| `GET /api/admin/agents/{agent_id}/monitoring` | Every series for one time range on a shared bucket grid: `buckets`, lifecycle `spans` and a `summary`. Takes `window` (`1h`, `3h`, `8h`, `12h`, `24h`, `3d`, `7d`; default `8h`, up to the present) **or** `start` and `end` (at least 5 minutes, clamped to the present and to retention). **422** for both, an unknown window, only one of `start` and `end`, or too short a range |
| `GET /api/admin/agents/{agent_id}/queries` | The runs that were running or waiting between `start` and `end` (both required), as a page. `sort` by `started_at`, `duration`, `wait`, `peak_memory`, `cpu_time`, `spill` or `bytes_read`, with `dir`; runs without a value sort last. Optional `status`, `cursor` and `limit` (at most 200). Needs `use` and `queries:admin`, because it lists other workspaces' SQL |

## Related

- [Errors](errors.md) — every error code.
- [API versions](api-versions.md) — what each `api_version` changed.
- [Service accounts & tokens](../access/service-accounts.md) — getting a token.
- [SQL support](sql-support.md) — what SQL the API accepts.
