# Command-line quickstart

`dh` is DuckHaven's command-line interface. It gives analysts, CI pipelines and operators scriptable
access to everything the API offers: running SQL, browsing the catalog, publishing dbt lineage and
semantic models, managing grants, and operating service accounts and agents.

## Install

```sh
pip install duckhaven-cli
```

Or, for an isolated tool install that does not touch a project's environment:

```sh
uv tool install duckhaven-cli
```

It runs on Python 3.10 and newer. Check it landed:

```sh
dh --version
```

## Sign in

```sh
dh auth login --host https://duckhaven.example.com
```

`dh` asks for your email and password, mints a personal access token for you, and writes it to
`~/.config/duckhaven/config.toml` with mode `0600`. The sign-in session itself is never stored — it
exists for the three requests it takes to issue the token, then is discarded.

!!! note "Single sign-on deployments"
    If your DuckHaven authenticates only through an identity provider, there is no password for `dh`
    to collect. Ask an administrator for a [service-account token](../guides/service-accounts.md) and
    run `dh auth login --host https://... --token dh_pat_...` instead.

Confirm it worked, and see which credential is in play:

```sh
dh auth status
dh auth describe
```

`dh auth describe` is the one to reach for when something is not behaving: it prints every setting
and **where it came from** — a flag, an environment variable, or which profile.

### Your tokens

`dh auth status` warns when the token you are using is within a fortnight of expiring, so a scheduled
job does not discover it with a 401. To see and manage them:

```sh
dh auth tokens              # what you hold, and which one is in use
dh auth revoke <id>         # retire one, including the one you are using
```

The listing never shows a token's value, and cannot: only a hash of it is stored. A token is shown
once, when it is issued — if you lose it, issue a new one with `dh auth login` and revoke the old.

## Set a default workspace

Most commands act on a workspace. Set one once rather than passing `--workspace` every time:

```sh
dh workspace list
dh auth login --host https://duckhaven.example.com --workspace analytics
```

Or edit the profile directly:

```toml
default_profile = "default"

[profile.default]
host      = "https://duckhaven.example.com"
token     = "dh_pat_..."
workspace = "analytics"
catalog   = "main"
```

## Run a query

```sh
dh sql -q "select count(*) from sales.orders"
```

`dh sql` submits the query, waits for it to finish, and prints every page of results. From a file, or
from a pipe:

```sh
dh sql -f report.sql
echo "select 1" | dh sql -i
```

Run `dh sql` with no arguments in a terminal and you get an interactive shell.

## Browse the catalog

```sh
dh catalog list
dh schema list
dh table list sales
dh table get sales.orders
dh table sample sales.orders --limit 20
```

A table is `schema.table`, or `catalog.schema.table` when you want a catalog other than your default
for one command.

## Use it from a script

Two things make `dh` safe to automate.

**One output shape.** `--format json` wraps every response the same way, so `jq` works the same
against every command:

```sh
dh query list --status failed --format json | jq '.data[].id'
```

```json
{
  "data": [],
  "cursor": null,
  "has_more": false
}
```

Output defaults to a table on a terminal and to JSON when piped or redirected, so this works whether
or not you remember the flag. `--all` walks every page rather than returning the first one.

**Exit codes that mean something.** In particular, a query that runs and fails exits `6`, so a
pipeline can tell bad SQL from a broken CLI or an unreachable server:

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

### Credentials in CI

`dh` reads `DH_HOST`, `DH_TOKEN` and `DH_WORKSPACE` from the environment, so a pipeline needs no
config file at all:

```sh
export DH_HOST=https://duckhaven.example.com
export DH_TOKEN=dh_pat_...
export DH_WORKSPACE=analytics
dh sql -q "select 1"
```

!!! warning "Do not use your personal token for CI"
    `dh auth login` mints a token tied to **you**: it expires, and it stops working the day you
    leave. Have an administrator create a [service account](../guides/service-accounts.md) and issue
    a token for that instead.

## Where to go next

- [CLI reference](../reference/cli.md) — every command and flag
- [Import lineage from dbt](../guides/import-dbt-lineage.md) — publishing from CI
- [Import semantics from dbt](../guides/import-dbt-semantics.md)
- [Service accounts & tokens](../guides/service-accounts.md) — credentials for automation
