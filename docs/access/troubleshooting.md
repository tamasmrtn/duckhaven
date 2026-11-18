---
type: troubleshooting
---

# Troubleshoot sign-in and access

Find the message you see on the setup or login screen, in the results pane, or in an API response, then follow what to
do.

How the access layers fit together is in [How access works](how-access-works.md). API errors arrive as
`{"error": …, "message": …, "details": …}`; the headings below quote the `message`, or the text on screen.

## "Invalid or missing setup token."

**What it means.** The setup screen received a token that does not match the one this stack generated.

**What to do.** Read the token again in the directory you started the stack from, and paste the whole line:

--8<-- "setup-token.md"

## "Setup token not available. The stack may need a fresh init."

**What it means.** No admin exists, but the token file is missing. It is consumed when the first admin is created,
and lives in the `api_data` volume, so a replaced container or a lost volume takes it with it.

**What to do.** On a stack with no data you want to keep, run `docker compose down -v` and start it again to get a new
token. Where the API's filesystem is not persistent, set `SETUP_TOKEN` in `.env` to a value of your own (see
[Configuration](../reference/configuration.md#control-plane-compose-env)).

## "Setup already complete: an admin user exists."

**What it means.** The first admin was already created, so the setup screen is closed for good.

**What to do.** Sign in with that admin. Lost its password? See
[Offboarding & break-glass](offboarding.md#break-glass-signing-in-when-the-idp-is-down).

## "Invalid credentials."

**What it means.** The email and password did not match a local account or, with LDAP enabled, a directory account. A
wrong password, an unknown email and an unreachable directory all look the same, by design.

**What to do.** Check the email and password. For a directory account, see
[Connect LDAP / AD](connect-ldap.md#if-it-goes-wrong). A deactivated account cannot sign in until an admin reactivates
it.

## "Too many sign-in attempts. Try again in ⟨n⟩ seconds."

**What it means.** Too many failed attempts were made for this account, or from your address, within the last minute,
so the email and password form refuses more until the minute is up. The limits are in
[Errors](../reference/errors.md#signing-in-and-tokens).

**What to do.** Wait the number of seconds shown, then sign in again. If it happens for an account nobody is signing in
to, someone else is trying its password. If every user behind a reverse proxy is refused at once, they share the
proxy's address: set `TRUSTED_PROXY_INGRESS=true` as in
[Reverse proxy & TLS](../deployment/reverse-proxy-tls.md#what-the-agent-sees).

## "Single sign-on failed. Try again or sign in locally."

**What it means.** The identity provider's sign-in did not complete: the redirect address, a claim, or the provider
itself failed.

**What to do.** See [Connect an IdP](connect-idp.md#if-it-goes-wrong). Admins with a local account can still sign in
with the email and password form.

## Sign-in seems to succeed, then returns to the login page

**What it means.** The browser refused the session cookie. That happens when `COOKIE_SECURE=true` but users reach
DuckHaven over plain HTTP at an address other than `localhost`: a browser accepts a `Secure` cookie only over HTTPS.

**What to do.** Keep `COOKIE_SECURE=true` only when users reach DuckHaven over HTTPS, set it to `false` otherwise, and
run `docker compose up -d`. See [Reverse proxy & TLS](../deployment/reverse-proxy-tls.md).

## `403` "Forbidden" on a workspace

**What it means.** The account is not a member of the workspace. A global `admin` is no exception.

**What to do.** Add the account to the workspace with a role; see
[Manage users & access](manage-users.md#give-someone-access-to-a-workspace).

## "Not authorized (reader) on ⟨catalog⟩.⟨schema⟩.⟨table⟩"

**What it means.** The catalog is scoped and you have no grant of that tier on that table. The tier in brackets is the
one the query needed: `reader` to read, `writer` to change.

**What to do.** Ask a workspace owner for a grant on the table, its schema or its catalog. See
[Scoped access](scoped-access.md#grants-and-tiers).

## "Enumerating metadata is not available in a scoped catalog…"

**What it means.** The query asks the engine to list objects — `information_schema`, `SHOW TABLES`, `duckdb_tables()`,
`pg_catalog` views such as `pg_tables`, and similar — in a workspace where at least one catalog is scoped. The message
names the scoped catalog, even when you were working in an open one.

**What to do.** Browse the catalog tree, or use `DESCRIBE ⟨catalog⟩.⟨schema⟩.⟨table⟩` for columns. For tools that need
`information_schema`, see [Scoped access](scoped-access.md#the-rejection-covers-the-whole-workspace).

## "Reading files directly is not available in a workspace with a scoped catalog…"

**What it means.** The query reads files by path — `read_parquet(…)`, `read_csv(…)` or a quoted path — which bypasses
table grants.

**What to do.** Query the tables instead. See [Scoped access](scoped-access.md#files-are-read-through-tables).

## "You don't have access to the data this query read, so its results can't be shown."

**What it means.** Opening a result is checked like running its query, against your current grants. You lack a grant on
a table the query read, or one was revoked since it ran.

**What to do.** Ask for a grant on the tables the query read, or run a query on tables you can read. See
[Scoped access](scoped-access.md#opening-a-result-needs-the-same-grants-as-running-its-query).

## "Reading the duckhaven system catalog needs the audit:read and queries:admin permissions."

**What it means.** The query reads the [system catalog](../querying/system-catalog.md), and your account lacks one of
the two permissions. A query the assistant runs for you needs them for you as well as for itself.

**What to do.** Ask an admin for the `admin` role, which holds both.

## "The duckhaven system catalog is read-only: only a SELECT may name it."

**What it means.** The statement writes to it, creates a table or view from it (`CREATE TABLE … AS`,
`INSERT … SELECT`), or `EXPLAIN`s a query on it.

**What to do.** Read it with a `SELECT`. To keep a copy, download the result.

## "The duckhaven system catalog cannot be the current catalog or on the search path. …"

**What it means.** A `USE`, `SET search_path` or `SET schema` statement names it, or the query was sent with
`duckhaven` as its catalog.

**What to do.** Keep a workspace catalog current, and name the system tables in full, as in `duckhaven.access.audit`.

## "Name the duckhaven system catalog's tables in full: …"

**What it means.** The query names `duckhaven` other than in `duckhaven.access.audit`, `duckhaven.access.grants` or
`duckhaven.query.history`: a two-part name such as `duckhaven.audit`, another schema such as `duckhaven.pg_catalog`,
or an alias or column called `duckhaven`.

**What to do.** Write the three-part name, and rename the alias or column.

## "The statement names the duckhaven system catalog and could not be parsed."

**What it means.** DuckHaven cannot tell what the statement does with it. Either it does not parse, or it is a `PIVOT`
in DuckDB's short form (`PIVOT duckhaven.access.audit ON …`), which DuckDB rewrites before it can be checked.

**What to do.** Fix the syntax, or write the pivot in the standard form:

```sql
SELECT *
FROM duckhaven.access.audit
PIVOT (count(*) FOR outcome IN ('success', 'denied'));
```

## "This action on agent '⟨name⟩' requires the '⟨tier⟩' tier."

**What it means.** Restarting, terminating or administering an agent needs a tier above `use` on it.

**What to do.** Ask someone with `admin` on the agent, or an admin with `agents:manage`, for a grant. See
[Agent access](agent-access.md).

## An agent is missing from the engine picker

**What it means.** The agent is `restricted` and you hold no grant on it, so it is hidden rather than shown as
forbidden.

**What to do.** Ask for `use` on the agent, or for your workspace to be granted it. See
[Agent access](agent-access.md#open-and-restricted-agents).

## Related

- [How access works](how-access-works.md)
- [Permissions reference](../reference/permissions.md)
- [Troubleshooting the deployment](../operations/troubleshooting.md)
