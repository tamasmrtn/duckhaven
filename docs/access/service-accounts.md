---
type: how-to
---

# Service accounts & tokens

Create a service account, give it access to a workspace, and call the API with its token, for CI pipelines,
schedulers and other unattended clients.

A service account is a non-human principal. It has a global role and workspace roles like a person, is checked and
audited the same way, and signs in only with a personal access token (`dh_pat_…`), never a password. A person who wants
a token for their own use, for [the command line](../getting-started/cli-quickstart.md), runs `dh auth login` instead.
Use a service account where a token tied to a person would break the day they leave.

## Before you begin

- **You need:** the `service_accounts:manage` permission to create the account and issue tokens, and `users:manage`, or
  the workspace `owner` role, to give it workspace access.
- **Applies to:** every deployment. Tokens are DuckHaven's own; they need no identity provider.

## Create the service account

1. Open **Admin** → **Service accounts** → **New service account**.
2. Enter a **Name**, such as `ci-runner`, and leave the **Role** at `user`.
3. Click **Create service account**.

The account appears in the list with the email `ci-runner@service-account.local`, which is an identifier, not a
mailbox. With the `user` role it holds no global permissions and can do nothing until you give it workspace access.
Give it `admin` only if it really administers DuckHaven.

## Give it access to a workspace

1. Open the account's **⋯** → **Manage workspaces**.
2. Choose a role in each workspace it needs — `reader` to run queries, `writer` to change tables. The change saves
   immediately.

In a [scoped catalog](scoped-access.md), it also needs a grant on the tables it reads, like any member.

## Issue a token

1. Open the account's **⋯** → **Manage tokens**.
2. Choose how long the token lasts under **Expires**, then click **Issue token**. Prefer a bounded expiry, so a leaked
   token stops working on its own.
3. Copy the `dh_pat_…` value with **Copy token** and store it in your client's secret store. It is shown once:
   DuckHaven keeps only a hash.

## Check that it worked

Ask the API who the token belongs to:

```bash
export DH=https://duckhaven.example.com
export DH_TOKEN=dh_pat_…

curl -s -H "Authorization: Bearer $DH_TOKEN" "$DH/api/me"
```

```json
{
  "email": "ci-runner@service-account.local",
  "name": "ci-runner",
  "role": "user",
  "auth_provider": "service_account",
  "is_active": true,
  "permissions": []
}
```

(Some fields omitted.) Then run a query in the workspace and read its rows. `wait_timeout_s` makes the API answer once
the query has finished, up to that many seconds:

```bash
curl -s -X POST "$DH/api/workspaces/analytics/queries" \
  -H "Authorization: Bearer $DH_TOKEN" -H "Content-Type: application/json" \
  -d '{
        "sql": "SELECT customer, sum(amount) AS revenue FROM sales.analytics.orders GROUP BY 1 ORDER BY 2 DESC",
        "wait_timeout_s": 30
      }'
```

The response is the query, with `"status": "done"` and its `id`. Read the result with that id:

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" "$DH/api/queries/⟨id⟩/rows?limit=100"
```

```json
{
  "rows": [
    {"customer": "initech", "revenue": "310.00"},
    {"customer": "acme", "revenue": "162.25"},
    {"customer": "globex", "revenue": "75.50"}
  ],
  "columns": ["customer", "revenue"],
  "column_schema": [
    {"name": "customer", "type": "VARCHAR"},
    {"name": "revenue", "type": "DECIMAL(38,2)"}
  ],
  "cursor": null,
  "total": 3
}
```

`DECIMAL` values arrive as exact strings; `column_schema` gives each column's type. The
[REST API reference](../reference/rest-api.md) describes paging and the other result formats. Send the token only in
the `Authorization` header: DuckHaven does not accept it as a query parameter or a cookie, because those end up in
proxy and access logs.

Every call the token makes is recorded against the service account in the workspace's
[query history](../querying/run-queries.md#find-an-earlier-run).

## Rotate or revoke a token

- **Rotate:** issue a new token, switch the client to it, then revoke the old one. Tokens are independent, so the two
  can overlap with no downtime.
- **Revoke one token:** **⋯** → **Manage tokens**, then revoke it under **Active tokens**. The next request with it
  gets `401`.
- **Stop every token at once:** **⋯** → **Deactivate**. Nothing is deleted, and reactivating restores the tokens. This
  is the fastest response to a leaked token.
- **Delete the account:** **⋯** → **Delete**. An account that has run queries keeps that history, so it cannot be
  deleted; deactivate it instead.

## If it goes wrong

### `401 Unauthorized`

The token is wrong, expired or revoked, or the account is deactivated. Check that the client sends
`Authorization: Bearer dh_pat_…` exactly, then issue a new token if needed.

### `403` with `"message": "Forbidden"` on a workspace

The account is not a member of that workspace. Give it a role under **⋯** → **Manage workspaces**.

### The query is refused for one table

In a scoped catalog the account needs a grant on every table the query reads. See
[Troubleshoot sign-in and access](troubleshooting.md).

## Related

- [How access works](how-access-works.md) — the layers a service account passes through.
- [Command-line quickstart](../getting-started/cli-quickstart.md) — using a token from `dh` in CI.
- [REST API](../reference/rest-api.md) — every endpoint.
