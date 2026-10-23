---
type: reference
---

# Errors

Every API error has an HTTP status and a stable `error` code; this page lists each code, what causes it and what to do.

A client should branch on `error`, never on `message`: the code stays fixed while the wording can improve. The
messages in `monospace` are the exact text the API sends, so you can search for what you see.

## The error body

Every `4xx` and `5xx` response has the same three fields:

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" "$DH/api/workspaces/nope/queries"
```

```json
{"error": "not_found", "message": "Workspace not found", "details": null}
```

| Field | Meaning |
|---|---|
| `error` | A stable `snake_case` code from the tables below. |
| `message` | Text that is safe to show a user. |
| `details` | Structured context where an endpoint documents it, otherwise `null`. |

A request that fails validation answers **422** `validation_error`, with each problem in `details.errors`:

```json
{
  "error": "validation_error",
  "message": "The request body or parameters did not validate.",
  "details": {"errors": [{"type": "missing", "loc": ["body", "sql"], "msg": "Field required"}]}
}
```

## General codes

When an endpoint has nothing more specific to say, the code follows the status. Unexpected server failures also arrive
in the envelope, as `internal_error` with a fixed message; the details are in the API's logs.

| Code | Status | Meaning |
|---|---|---|
| `bad_request` | 400, 405 and any other 4xx | The request is wrong in a way no specific code covers, such as the wrong HTTP method. |
| `unauthorized` | 401 | No credentials, or a token that is wrong, expired or revoked, or an account that is deactivated. |
| `forbidden` | 403 | You are signed in but lack the role or permission. On a workspace route: you are not a member. |
| `not_found` | 404 | The object does not exist, or you may not know it exists. Hidden agents and tables you hold no grant on answer 404. |
| `conflict` | 409 | The object's current state does not allow the change. |
| `gone` | 410 | `Result no longer available`: the query's result was deleted after its retention period. Run the query again. |
| `unprocessable_content` | 422 | The request is well formed but cannot be done. Query history also uses it for a malformed `cursor`. |
| `validation_error` | 422 | The body or parameters do not match the schema; see `details.errors`. |
| `internal_error` | 500 | An unexpected server failure. Report it with the time and the request. |
| `upstream_error` | 502 | A service the API depends on failed. |
| `unavailable` | 503 | `Agent not connected`: the agent you named is offline and cannot be started. The run is recorded as failed, with its id in `details.query_id`. |
| `upstream_timeout` | 504 | `The agent did not answer in time.` Retry; if it repeats, check the agent's load. |

## Signing in and tokens

| Code | Status | Meaning and what to do |
|---|---|---|
| `session_required` | 403 | Issuing yourself a token needs a signed-in session, not a bearer token. Sign in, or use a [service-account token](../access/service-accounts.md). |
| `service_account_tokens_are_managed` | 403 | A service account cannot manage its own tokens. An admin issues them under **Admin** → **Service accounts**. |
| `too_many_tokens` | 409 | `You already hold 25 tokens, the maximum.` Revoke one you do not use. |

See [Troubleshoot sign-in and access](../access/troubleshooting.md) for sign-in failures.

## Access to data and agents

| Code | Status | Meaning and what to do |
|---|---|---|
| `grant_denied` | 403 | A [scoped catalog](../access/scoped-access.md) refused the query or result: `Not authorized (reader) on ⟨catalog⟩.⟨schema⟩.⟨table⟩`, a metadata listing, or a direct file read. The message says which. |
| `agent_forbidden` | 403 | `This action on agent '⟨name⟩' requires the '⟨tier⟩' tier.` Ask for a higher [agent tier](../access/agent-access.md). |

Each message has an entry in [Troubleshoot sign-in and access](../access/troubleshooting.md).

## Queries and SQL

| Code | Status | Meaning and what to do |
|---|---|---|
| `sql_not_allowed` | 422 | The statement type is not allowed, such as `ATTACH`, `INSTALL` or `SET`. See [SQL support](sql-support.md). |
| `statement_not_allowed` | 422 | The same check in a [SQL session](../querying/sql-sessions.md), which allows a few more statements. |
| `invalid_parameters` | 422 | The [query parameters](sql-support.md#query-parameters) do not fit the placeholders. |
| `agent_required` | 422 | `agent_id is required`: no agent is connected and elastic compute is off. Start an agent or name one. |
| `catalog_read_only` | 409 | A [storage migration](../data/migrate-catalog-storage.md) is running, so writes are refused. Reads still work. |
| `not_describable` | 422 | [Describe](rest-api.md#describing-a-query) cannot type this statement: several statements, a `DESCRIBE`/`SHOW`/`PRAGMA`, or SQL that does not bind. |
| `too_many_tables` | 422 | The [metadata report](rest-api.md#workspace-metadata) with columns matched more than 1,000 tables. Narrow it with `catalog`, `schema` or `table`. |
| `invalid_cursor` | 422 | `Malformed cursor.` Send back the cursor exactly as you received it. |
| `stale_cursor` | 422 | `The row this cursor points at no longer exists. Start again.` Page again from the start. |

## Compute and agents

| Code | Status | Meaning and what to do |
|---|---|---|
| `compute_starting` | 503 | `Compute is starting; retry shortly.` [Elastic compute](../compute/elastic-compute.md) is starting an agent. Retry after the `Retry-After` header; the retry lands on the same agent. |
| `compute_unavailable` | 503 | `Could not start compute for this session.` or `Could not start the configured agent.` Check the compute backend. |
| `agent_incompatible` | 422 | The agent lacks an extension a catalog needs. The message names the extension and the catalog. |
| `runtime_retired` | 409, 422 | The agent runs a [runtime](../compute/runtimes.md) that is retired. Create an agent on a supported runtime. |
| `runtime_unsupported` | 422 | The agent is not running a supported runtime. The message names the runtime it reports. |
| `ducklake_format_unsupported` | 422 | The agent's [runtime](../compute/runtimes.md#mixing-runtimes-on-the-same-data) can't open a DuckLake catalog in the workspace as it is, or would create a catalog the default runtime can't open. The message names the catalog and its format. Send the work to an agent on the default runtime. |
| `agent_sandbox_unverified` | 422 | The agent could not lock its DuckDB configuration, so it cannot hold a SQL session. It still runs queries; its logs say why the lock failed. |
| `session_not_open` | 409 | `session is ⟨status⟩`: the SQL session is still starting, or closed. Wait for `open`, or open a new one. |
| `session_open_failed` | 503 | The agent failed to open the SQL session; the message carries its reason. |
| `staging_unavailable` | 422 | `session backend has no staging location`: the session's catalog has no storage that can stage uploaded files. |

## Saved work

| Code | Status | Meaning and what to do |
|---|---|---|
| `saved_query_exists` | 409 | A saved query with that name exists (names ignore case). `details` has its `id` and `name`. |
| `worksheet_conflict` | 409 | The worksheet changed elsewhere since your `base_version`. `details.current` has the latest version. |
| `base_version_required` | 422 | Saving `sql` or `title` needs the `base_version` you edited. |
| `saved_query_not_found`, `query_not_found` | 422 | A worksheet field names a saved query or query that is not in this workspace. |

## Semantic layer

| Code | Status | Meaning and what to do |
|---|---|---|
| `semantic_error` | 422 | A definition or compile request is invalid. The message names the legal alternatives. |
| `validation_failed` | 422 | `This model cannot be published until its definitions resolve.` Validate it and fix what is broken. |
| `imported_model` | 409 | The model came from an import. Change it at the source and import again. |
| `dataset_in_use`, `dimension_in_use` | 409 | Other definitions depend on it; the message names them. Remove those first. |

## Administration

| Code | Status | Meaning and what to do |
|---|---|---|
| `ducklake_disabled` | 422 | DuckLake catalogs are switched off. Set `DUCKLAKE_ENABLED=true`; see [DuckLake](../deployment/ducklake.md). |
| `elastic_disabled` | 409 | `Elastic compute is not enabled.` |
| `invalid_size` | 422 | The CPU or memory size for an elastic agent is out of range. |
| `not_restartable`, `not_terminable` | 409 | Only a terminated elastic agent can be restarted, and only a running one terminated. |
| `provision_failed` | 502 | The compute backend failed to create or restart the agent. Check its logs. |
| `unknown_runtime`, `runtime_unavailable` | 422 | The requested runtime does not exist, or is deprecated or retired, so new agents cannot use it. |
| `runtime_beta` | 422 | The runtime is in beta. Send `allow_beta: true` to use it. |

## Related

- [REST API](rest-api.md) — the conventions every endpoint follows.
- [API versions](api-versions.md) — how the error body changed in `api_version` 2.
- [Troubleshoot sign-in and access](../access/troubleshooting.md)
- [Troubleshooting the deployment](../operations/troubleshooting.md)
