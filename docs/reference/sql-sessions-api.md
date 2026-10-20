---
type: reference
---

# SQL sessions API

The endpoints a driver uses to open a SQL session, run statements in it, stage files and close it.

What a session is, and what it may run, is in [SQL sessions](../querying/sql-sessions.md). The endpoints need
`SQL_SESSIONS_ENABLED=true`; without it every one answers **404** "SQL sessions are not enabled". They follow the
[REST API conventions](rest-api.md#conventions): a bearer token, and errors as `{"error", "message", "details"}`.

## Endpoints

| Method and path | Does |
|---|---|
| `POST /api/workspaces/{workspace}/sql/sessions` | Open a session (**201**), or **202** while compute starts |
| `GET /api/sql/sessions/{session_id}` | The session and its status |
| `POST /api/sql/sessions/{session_id}/statements` | Run one statement (**200** finished, **202** still running) |
| `POST /api/sql/sessions/{session_id}/staging-files` | Presigned URLs to upload files to the session's staging prefix (**201**) |
| `DELETE /api/sql/sessions/{session_id}` | Close the session (**204**) |
| `GET /api/workspaces/{workspace}/sql/sessions` | The workspace's sessions, as a [page](rest-api.md#pagination) |
| `GET /api/sql/sessions/{session_id}/statements` | The session's statements, as a page |

A statement is an ordinary query: read its status at `GET /api/queries/{query_id}` and its rows at
`GET /api/queries/{query_id}/rows`, as for any query.

Every workspace member can read a session and its statements. Only the member who opened it can run statements or
stage files in it; anyone else gets **403**.

## Opening a session

```bash
curl -s -X POST "$DH/api/workspaces/analytics/sql/sessions" \
  -H "Authorization: Bearer $DH_TOKEN" -H "Content-Type: application/json" \
  -d '{"catalog": "sales"}'
```

**201** with the open session (some fields omitted):

```json
{
  "id": "0934b473-7f4c-47a0-880a-e89dbf8060c9",
  "status": "open",
  "agent_id": "c1e3b8f4-2ba4-4a78-afc6-c2e037d3f802",
  "active_catalog": "sales",
  "staging_uri": "s3://warehouse/_staging/0934b473-7f4c-47a0-880a-e89dbf8060c9/",
  "client_name": "curl",
  "client_version": "8.23.0",
  "close_reason": null
}
```

| Field | Default | Meaning |
|---|---|---|
| `catalog` | the workspace's default | The catalog unqualified names resolve in |
| `agent_id` | chosen by the server | Run on this agent; you need the `use` tier on it |
| `use_cache` | `true` | `false` turns the [result cache](../querying/result-cache.md) off for every statement |
| `wait_timeout_s` | `SQL_SESSION_WAIT_TIMEOUT_S` (45) | How long to wait while compute starts. `0` never waits. Above `SQL_SESSION_MAX_WAIT_TIMEOUT_S` (120) is a **422** |
| `on_wait_timeout` | `cancel` | What happens when that wait runs out; see below. `0` with `cancel` is a **422** |

`client_name` and `client_version` come from the request's `User-Agent`; see
[Client and tags](rest-api.md#client-and-tags).

### Waiting for compute

With [elastic compute](../compute/elastic-compute.md) and no agent running, the open starts one and waits. The session
is `pending` until an agent claims it, then `opening`, then `open`.

| Outcome | Status | Body |
|---|---|---|
| Opened within the wait | **201** | The open session |
| Wait ran out, `on_wait_timeout=cancel` | **503** with `Retry-After` | `compute_starting` |
| Wait ran out, `on_wait_timeout=continue` | **202** | The session, still `pending` or `opening` |
| Compute could not be started | **503** | `compute_unavailable` |

After a **202**, poll `GET /api/sql/sessions/{session_id}` until `status` is `open`. A statement sent before then is a
**409** `session_not_open`. After a **503** `compute_starting`, retry after `Retry-After` seconds: the agent keeps
starting, and the retry lands on it.

## Running a statement

```bash
curl -s -X POST "$DH/api/sql/sessions/$SESSION/statements" \
  -H "Authorization: Bearer $DH_TOKEN" -H "Content-Type: application/json" \
  -d '{"sql": "SELECT customer, sum(amount) AS revenue FROM analytics.orders GROUP BY 1 ORDER BY 2 DESC",
       "first_page_limit": 2}'
```

**200** with the finished statement, its first page of rows included (some fields omitted):

```json
{
  "id": "12411352-8b25-4ce0-a382-1191a8a151e0",
  "status": "done",
  "origin": "session",
  "session_id": "0934b473-7f4c-47a0-880a-e89dbf8060c9",
  "row_count": 3,
  "first_page": {
    "rows": [{"customer": "initech", "revenue": "310.00"}, {"customer": "acme", "revenue": "162.25"}],
    "columns": ["customer", "revenue"],
    "column_schema": [{"name": "customer", "type": "VARCHAR"}, {"name": "revenue", "type": "DECIMAL(38,2)"}],
    "cursor": "2",
    "total": 3
  }
}
```

| Field | Default | Meaning |
|---|---|---|
| `sql` | required | One or more statements the [policy](sql-support.md#allowed-statements) allows |
| `timeout_s` | `600` | Cancel the statement after this many seconds |
| `wait_timeout_s` | `SQL_STATEMENT_WAIT_TIMEOUT_S` (10) | How long to wait for it to finish. `0` never waits. Above `SQL_STATEMENT_MAX_WAIT_TIMEOUT_S` (60) is a **422** |
| `first_page_limit` | `SQL_STATEMENT_FIRST_PAGE_LIMIT` (200) | Rows to return as `first_page`. `0` returns none. Above 200 is a **422** |
| `inline_format` | `json` | `arrow` returns the whole result as `inline_arrow` instead; see below |
| `use_cache` | `true` | `false` runs the statement even when the cache could answer it |
| `parameters` | – | Values for placeholders; see [Query parameters](sql-support.md#query-parameters) |
| `tags` | – | Labels on the run; see [Client and tags](rest-api.md#client-and-tags) |

### Waiting for a statement

The call holds its response until the statement finishes or `wait_timeout_s` runs out:

- **200**: the statement finished, `done` or `failed`. A failed statement's error is on the row.
- **202**: it is still running. Poll `GET /api/queries/{query_id}`, which also takes `wait_timeout_s` as a query
  parameter so each poll can wait for the end. The statement is never cancelled for outlasting the wait.

Set the wait above how long your statements usually take, or to `0`. A wait that most statements outlast costs the hold
and the polling both: at 48 concurrent clients, a 1-second wait ran 27% slower than `0`.

`has_result` on the response says whether there are rows: `false` for DDL and DML, so the client need not ask.

### First page of rows

`first_page` has the shape of `GET /api/queries/{query_id}/rows`, `cursor` included, so a larger result continues from
it with `/rows?cursor=…`. A statement whose result fits in the page costs one call. `first_page` is absent for DDL and
DML, and when the rows could not be read; the statement still succeeded, and `/rows` says why. A client that will not
read the rows should send `first_page_limit: 0`, which keeps the response small.

### Arrow results

With `inline_format: "arrow"`, a result of at most `SQL_STATEMENT_INLINE_ARROW_MAX_BYTES` (256 KiB), both as a file and
as an Arrow stream, arrives as `inline_arrow`: a base64-encoded
[Arrow IPC stream](rest-api.md#whole-results-as-arrow-or-parquet), with `first_page_limit` ignored. Otherwise
`inline_arrow` is `null`, and the client reads `GET /api/queries/{query_id}/result`: for a larger result, one that could
not be read, or one whose statement outlasted the wait. `duckhaven-sql-connector` uses this when pyarrow is installed.

The limit is small because base64 makes the stream a third larger inside the JSON response. The
`duckhaven_statement_inline_results_total{outcome}` metric counts how results fared: `inlined`; `too_large`, not read
because the file or its estimated Arrow size was over the limit; `arrow_too_large`, read and encoded before it turned
out too big; `unsized` and `unavailable`, results from an agent that did not report a size and ones that could not be
read.

### Delivery deadlines

Two server-side deadlines resolve a statement whose messages are lost between the API and the agent:

| Deadline | Setting | The statement fails with |
|---|---|---|
| The agent confirms receipt, moving the statement from `queued` to `running` | `SQL_STATEMENT_ACK_DEADLINE_S` (15) | `agent did not ack statement` |
| A `running` statement finishes | its `timeout_s` plus `SQL_STATEMENT_TIMEOUT_GRACE_S` (30) | `statement exceeded timeout` |

Such a statement is never retried automatically, because it may have run. Closing the session, by the client, the
reaper or an agent disconnect, also fails any statement still `queued` or `running` in it.

## Staging files

A load uploads files to the session's staging prefix, then reads them with a statement.

```bash
curl -s -X POST "$DH/api/sql/sessions/$SESSION/staging-files" \
  -H "Authorization: Bearer $DH_TOKEN" -H "Content-Type: application/json" \
  -d '{"files": ["orders.parquet"]}'
```

**201**, with the signatures shortened:

```json
{
  "files": [{
    "name": "orders.parquet",
    "key": "s3://warehouse/_staging/0934b473-7f4c-47a0-880a-e89dbf8060c9/orders.parquet",
    "put_url": "http://localhost:19000/warehouse/_staging/0934b473-…/orders.parquet?X-Amz-Signature=…",
    "get_url": "http://objectstore:9000/warehouse/_staging/0934b473-…/orders.parquet?X-Amz-Signature=…"
  }],
  "expires_at": "2026-10-19T18:57:00.065819Z"
}
```

1. Upload each file with a plain `PUT` to its `put_url`. No storage SDK or credential is needed.
2. Load it through the session, reading the `get_url`, for example
   `INSERT INTO sales.analytics.orders SELECT * FROM read_parquet('⟨get_url⟩')`. The agent reads it with no credential
   of its own.

The URLs expire after `SQL_SESSION_STAGING_URL_TTL_S` (15 minutes). `put_url` uses the address clients reach the
object store at, `S3_ENDPOINT_PUBLIC` on the bundled stack; `get_url` uses the agent's. S3 and the bundled store sign
with SigV4, Azure with SAS. A request for a closed session is a **409**; a catalog whose storage cannot stage files is a
**422** `staging_unavailable`.

## Closing a session

`DELETE /api/sql/sessions/{session_id}` answers **204**, also for a session that has already ended. A later statement
answers **409** `session_not_open` with `session is closed`. Closing another member's session needs the
`queries:admin` permission, and otherwise answers **403**; anything it had in flight fails.

### Close reasons

`close_reason` on a finished session says why it ended:

| Reason | What happened |
|---|---|
| `client` | The client closed it |
| `idle` | No statement for `SQL_SESSION_IDLE_TIMEOUT_S`; usually a client that crashed or forgot to close |
| `max_lifetime` | Open longer than `SQL_SESSION_MAX_LIFETIME_S`, however busy it was |
| `open_timeout` | The agent never confirmed the open |
| `compute_timeout` | The open gave up while compute was still starting |
| `provisioning_timeout` | Compute never arrived |
| `agent_disconnect` | The agent holding it disconnected; everything in flight failed |
| `agent_lease` | The agent expired a session the API had lost track of |
| `failed` | The agent could not open the connection |

## Related

- [SQL sessions](../querying/sql-sessions.md) — the concept.
- [SQL support](sql-support.md) — the statements a session may run.
- [Errors](errors.md) — `session_not_open`, `compute_starting`, `statement_not_allowed` and others.
- [Configuration](configuration.md#sql-sessions) — the settings named here.
