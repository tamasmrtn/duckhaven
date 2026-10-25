---
type: tutorial
---

# Command-line quickstart

Install `dh`, DuckHaven's command-line interface, sign in, and run queries and catalog commands from a terminal or a
script.

`dh` reaches everything the API offers: running SQL, browsing catalogs, publishing dbt lineage and semantic models,
managing grants, and operating service accounts and agents. This tutorial uses the `analytics` workspace and the
`sales.analytics.orders` table from the [Quickstart](quickstart.md).

## Before you begin

- Python 3.10 or newer, with `pip` or [uv](https://docs.astral.sh/uv/).
- A DuckHaven account with a password, and the address of your DuckHaven, such as `https://duckhaven.example.com`. If
  your DuckHaven signs people in only through an identity provider, ask an administrator for a
  [service-account token](../access/service-accounts.md) instead; step 2 shows where it goes.

## 1. Install the CLI

```bash
pip install duckhaven-cli
```

Or, to keep it out of your project's environment, `uv tool install duckhaven-cli`. Check it landed:

```bash
dh --version
```

```text
0.2.0
```

## 2. Sign in

```bash
dh auth login --host https://duckhaven.example.com --workspace analytics
```

`dh` asks for your email and password, issues a personal access token, and stores the token — never your password —
in `~/.config/duckhaven/config.toml`, readable only by you. The token is valid for 90 days; `--expires-in-days` changes
that. `--workspace` makes `analytics` the default, so later commands need no `--workspace` flag.

With a token from an administrator instead, run
`dh auth login --host https://duckhaven.example.com --token dh_pat_… --workspace analytics`.

Confirm who you are signed in as:

```bash
dh auth status
```

```text
field             value
host              https://duckhaven.example.com
profile           default
user              admin@example.com
name              Admin
role              admin
workspace         analytics
token_expires_at  2027-01-17T14:23:12.113093Z
server_version    0.9.0
api_version       3
```

## 3. Run a query

```bash
dh sql -q "SELECT customer, sum(amount) AS revenue FROM sales.analytics.orders GROUP BY customer ORDER BY revenue DESC;"
```

```text
customer  revenue
initech   310.00
acme      162.25
globex    75.50
```

`dh sql` submits the query, waits for it to finish, and prints every page of the result. It also reads SQL from a file
(`dh sql -f report.sql`) or from a pipe (`echo "SELECT 1;" | dh sql -i`), and with no arguments at a terminal it opens
an interactive shell.

## 4. Browse the catalog

```bash
dh catalog list
dh table list analytics
dh table get analytics.orders
dh table sample analytics.orders
```

A table is `schema.table` in your default catalog, or `catalog.schema.table` for any other. `dh table get` prints the
table's columns, format and storage location; `dh table sample` returns a 20-row preview.

## 5. Use it from a script

Output is a table at a terminal and JSON when piped or redirected. Every JSON response has the same shape, so `jq`
works the same way against every command:

```bash
dh catalog list --format json | jq -c '.data[] | {slug, kind, is_default}'
```

```text
{"slug":"sales","kind":"iceberg_polaris","is_default":true}
```

The envelope is always `{"data": …, "cursor": …, "has_more": …}`; `--all` walks every page instead of returning the
first.

The exit code says what went wrong, so a pipeline can tell bad SQL from an unreachable server:

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | Bad flags or arguments |
| 3 | Authentication or permission |
| 4 | Not found |
| 5 | Rejected input, or a conflict |
| 6 | The query ran and failed |
| 7 | Timed out |
| 8 | Server unavailable |
| 9 | A destructive command was not confirmed, so nothing was sent |

Commands that destroy data — `workspace delete`, `catalog drop`, `catalog detach`, `schema drop`, `table drop`,
`lineage purge` and `semantic purge` — ask before acting at a terminal, and the default answer is no. Without a
terminal, in a pipeline or a cron job, there is nobody to ask: they exit `9` without sending anything. Pass `--yes`
when you mean it.

In CI, set `DH_HOST`, `DH_TOKEN` and `DH_WORKSPACE` instead of using a config file:

```bash
export DH_HOST=https://duckhaven.example.com
export DH_TOKEN=⟨a service-account token⟩
export DH_WORKSPACE=analytics
dh sql -q "SELECT count(*) FROM sales.analytics.orders;"
```

Use a [service-account token](../access/service-accounts.md) there, not your own: a personal token expires and stops
working when you leave.

## If it goes wrong

### A command says it is not authenticated, or exits `3`

Your token expired or was revoked. `dh auth status` warns two weeks before a token expires. Sign in again with
`dh auth login`, and retire the old token with `dh auth revoke ⟨id⟩`; `dh auth tokens` lists yours.

### A setting is not what you expect

`dh auth describe` prints every setting and where it came from: a flag, an environment variable, or a profile in the
config file.

## Next steps

- [CLI reference](../reference/cli.md) — every command and flag.
- [Import lineage from dbt](../metadata/import-dbt-lineage.md) — publish lineage from CI.
- [Service accounts & tokens](../access/service-accounts.md) — credentials for automation.
