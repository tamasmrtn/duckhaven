# CLI reference

Every `dh` command, its summary and its flags. Generated from the CLI's own command tree, so it
cannot drift from the binary it documents. New here? Start with
[the quickstart](../getting-started/cli-quickstart.md).

## Global options

Accepted before or after the subcommand, on every command.

| Option | Purpose |
|---|---|
| `--profile` | Named profile to use. |
| `--host` | DuckHaven base URL. |
| `--workspace/-w` | Workspace slug or UUID. |
| `--catalog` | Catalog to resolve names in. |
| `--format` | `json`, `table` or `csv`. Defaults to a table on a terminal, JSON otherwise. |
| `--output` | Write the payload to a file instead of stdout. |
| `--quiet` | Suppress progress and warnings on stderr. |
| `--no-color` | Disable colour. `NO_COLOR` does the same. |
| `--debug` | Trace settings resolution on stderr. |
| `--version` | Print the `dh` version and exit. |

## `dh admin`

Operator tasks: accounts, users, agents.

### `dh admin agent`

Compute agents.

#### `dh admin agent access`

Who may use this agent, and how that was decided.

```text
<agent_id>
```

#### `dh admin agent bootstrap`

Mint a single-use token for an agent to register itself.

#### `dh admin agent compute-options`

The CPU and memory shapes this deployment will provision.

#### `dh admin agent delete`

Delete an agent registration.

```text
<agent_id>
```

#### `dh admin agent disconnect`

Force an agent's control channel closed. It may reconnect.

```text
<agent_id>
```

#### `dh admin agent elastic-create`

Provision an elastic agent. Accepted asynchronously; poll `admin agent get`.

```text
--cpu
--memory-gb
--idle-timeout-minutes
```

#### `dh admin agent get`

One agent's registration, capabilities and lifecycle state.

```text
<agent_id>
```

#### `dh admin agent list`

Every agent, including ones this caller could not run queries on.

#### `dh admin agent metrics`

Current load and capacity for every agent.

#### `dh admin agent monitoring`

One agent's monitoring detail.

```text
<agent_id>
```

#### `dh admin agent restart`

Restart an elastic agent.

```text
<agent_id>
```

#### `dh admin agent revoke-credential`

Revoke an agent's credential, so it cannot reconnect.

```text
<agent_id>
```

#### `dh admin agent terminate`

Terminate an elastic agent.

```text
<agent_id>
```

### `dh admin maintenance`

Deployment maintenance policy.

#### `dh admin maintenance policy`

The deployment's maintenance policy.

#### `dh admin maintenance scan`

Run a maintenance scan now rather than waiting for the schedule.

### `dh admin pat`

Tokens issued to a service account.

#### `dh admin pat issue`

Issue a token for a service account. The secret is shown once.

```text
<service_account_id>
--expires-in-days
```

#### `dh admin pat list`

Tokens issued to a service account: creation and expiry, never the secret.

```text
<service_account_id>
```

#### `dh admin pat revoke`

Revoke one token. It stops authenticating immediately.

```text
<service_account_id>
<pat_id>
```

### `dh admin service-account`

Machine identities for unattended callers.

#### `dh admin service-account create`

Create a service account.

```text
<name>
--role
```

#### `dh admin service-account delete`

Delete a service account.

```text
<service_account_id>
```

#### `dh admin service-account list`

Service accounts and how many live tokens each holds.

```text
--all
```

#### `dh admin service-account update`

Change a service account's role, or disable it.

```text
<service_account_id>
--role
--active
```

### `dh admin storage`

Storage backends.

#### `dh admin storage health`

Check a backend end to end, by vending credentials against a probe table.

```text
<storage_backend_id>
```

#### `dh admin storage list`

Configured storage backends.

### `dh admin user`

People and their workspace roles.

#### `dh admin user create`

Create a local user. The password is prompted for rather than passed as a flag.

```text
<email>
--name
--password
--role
```

#### `dh admin user list`

People known to the deployment.

```text
--all
```

#### `dh admin user remove-from-workspace`

Remove a user from a workspace.

```text
<user_id>
<workspace>
```

#### `dh admin user revoke-sessions`

Sign a user out everywhere. Their tokens are unaffected.

```text
<user_id>
```

#### `dh admin user set-workspace-role`

Add a user to a workspace, or change the role they hold there.

```text
<user_id>
<workspace>
<role>
```

#### `dh admin user update`

Change a user's global role, or deactivate them.

```text
<user_id>
--role
--active
```

#### `dh admin user workspaces`

Which workspaces a user belongs to, and in what role.

```text
<user_id>
```

## `dh api`

Call any endpoint directly.

### `dh api delete`

Send a DELETE request.

```text
<path>
--data/-d
--param/-p
```

### `dh api get`

Send a GET request.

```text
<path>
--data/-d
--param/-p
```

### `dh api patch`

Send a PATCH request.

```text
<path>
--data/-d
--param/-p
```

### `dh api post`

Send a POST request.

```text
<path>
--data/-d
--param/-p
```

### `dh api put`

Send a PUT request.

```text
<path>
--data/-d
--param/-p
```

## `dh auth`

Sign in, and inspect the credential in use.

### `dh auth describe`

Show which credential is in use and where each setting came from.

### `dh auth login`

Sign in and store a personal access token.

```text
--name
--email
--token
--workspace
--expires-in-days
```

### `dh auth logout`

Forget the stored token, keeping the rest of the profile.

```text
--name
```

### `dh auth revoke`

Revoke one of your own tokens, by the id `dh auth tokens` shows.

```text
<pat_id>
```

### `dh auth status`

Who the stored credential authenticates as, and how long it has left.

### `dh auth tokens`

Your own tokens: when each was issued, when it expires, which is in use.

## `dh catalog`

Catalogs attached to a workspace.

### `dh catalog attach`

Attach an existing catalog to the workspace.

```text
<catalog>
```

### `dh catalog create`

Create a catalog and attach it to the workspace.

```text
<name>
--storage-backend
--access-mode
```

### `dh catalog detach`

Detach a catalog from the workspace. The catalog itself survives.

```text
<catalog>
```

### `dh catalog drop`

Drop a catalog outright, by id. Destructive.

```text
<catalog_id>
```

### `dh catalog list`

Catalogs attached to the workspace.

```text
--all
```

### `dh catalog refresh-stats`

Recompute table statistics across the catalog.

## `dh grant`

Catalog access control.

### `dh grant access-mode`

Switch the catalog between open and scoped access.

```text
<mode>
```

### `dh grant list`

Who has what on the catalog, and in which access mode.

```text
--principals
```

### `dh grant remove`

Revoke one grant by its id, as shown by `dh grant list`.

```text
<grant_id>
```

### `dh grant set`

Grant a principal access to the catalog, a schema, or one table.

```text
--user
--tier
--schema
--table
```

## `dh health`

Liveness, readiness, and the deployment's own health report.

## `dh lineage`

Publish and retire lineage from other producers.

### `dh lineage import`

Publish a producer's own artifact, translated by that producer's adapter.

```text
<provider>
<artifact>
--catalog-json
--reconcile
```

### `dh lineage import-edges`

Publish already-canonical edges from a producer with no adapter.

```text
<file>
--provider
--run-id
--reconcile
```

### `dh lineage purge`

Remove every edge a retired producer asserted. Requires workspace owner.

```text
--provider
```

## `dh profile`

Inspect and edit local connection profiles.

### `dh profile list`

List the configured profiles and which one is the default.

### `dh profile remove`

Delete a profile and the token stored with it.

```text
<name>
```

### `dh profile show`

Show one profile. The token is reported as present, never printed.

```text
<name>
```

### `dh profile use`

Make a profile the default for subsequent commands.

```text
<name>
```

## `dh query`

Run SQL and inspect past runs.

### `dh query cancel`

Ask the agent to stop a running query.

```text
<query_id>
```

### `dh query get`

One run's status, timings and error.

```text
<query_id>
```

### `dh query list`

The query log, newest first. Doubles as the audit trail.

```text
--status
--statement-type
--since
--until
--origin
--session
--agent
--user
--search/-q
--slower-than
--sort
--dir
--all-workspaces
--limit
--all
```

### `dh query profile`

The execution profile captured for a finished run, if there was one.

```text
<query_id>
```

### `dh query rows`

The results of a finished run, following the cursor to the end.

```text
<query_id>
--limit
--all
```

### `dh query run`

Run SQL and print the results. The noun-first spelling of `dh sql`.

```text
--query/-q
--file/-f
--stdin/-i
--no-wait
--timeout
--limit
--all
--agent
```

## `dh saved-query`

Named SQL saved in the workspace.

### `dh saved-query create`

Save SQL under a name, replacing any query already using that name.

```text
<name>
--query/-q
--file/-f
--agent
```

### `dh saved-query delete`

Delete a saved query.

```text
<saved_query_id>
```

### `dh saved-query list`

The workspace's saved queries, newest first.

```text
--limit
--all
```

### `dh saved-query update`

Change a saved query's name, SQL or default agent. Omitted fields are left alone.

```text
<saved_query_id>
--name
--query/-q
--file/-f
--agent
```

## `dh schedule`

Cron schedules for saved queries.

### `dh schedule create`

Schedule a saved query to run on a cron expression.

```text
<saved_query_id>
--cron
--agent
--disabled
```

### `dh schedule delete`

Delete a schedule. The saved query it ran is left alone.

```text
<schedule_id>
```

### `dh schedule list`

The workspace's schedules.

### `dh schedule runs`

Runs produced by a schedule, or by all of them.

```text
<schedule_id>
--limit
--all
```

### `dh schedule update`

Change a schedule's cron, agent, or whether it runs at all.

```text
<schedule_id>
--cron
--agent
--enabled
```

## `dh schema`

Schemas within a catalog.

### `dh schema create`

Create a schema.

```text
<name>
```

### `dh schema drop`

Drop a schema.

```text
<name>
```

### `dh schema list`

Schemas in the catalog.

## `dh search`

Find catalogs, schemas, tables and saved queries by name.

```text
<query>
--limit
```

## `dh semantic`

Publish and manage semantic models.

### `dh semantic deprecate`

Retire a published model without deleting it.

```text
<model>
```

### `dh semantic import`

Publish semantic definitions from an external producer.

```text
<provider>
<artifact>
--reconcile
```

### `dh semantic model`

Semantic models.

#### `dh semantic model get`

One model in full: datasets, dimensions, metrics and relationships.

```text
<model>
```

#### `dh semantic model list`

The workspace's semantic models, published and draft.

### `dh semantic publish`

Make a model authoritative to the assistant. Validates first.

```text
<model>
```

### `dh semantic purge`

Remove everything one provider published. Requires workspace owner.

```text
--provider
```

### `dh semantic relationship`

Joins between a model's datasets.

#### `dh semantic relationship add`

Declare a join between two of a model's datasets.

```text
<model>
--name
--left
--right
--join
--cardinality
```

#### `dh semantic relationship remove`

Remove a relationship from a model.

```text
<model>
<name>
```

### `dh semantic validate`

Check a model without publishing it. Safe to run in CI.

```text
<model>
```

## `dh session`

Stateful SQL sessions, for dbt, dlt and the REPL.

### `dh session close`

Close a session and release its connection.

```text
<session_id>
```

### `dh session exec`

Run one statement on an existing session's connection.

```text
<session_id>
--query/-q
--file/-f
--timeout
--limit
--all
```

### `dh session get`

One session's status and the agent holding it.

```text
<session_id>
```

### `dh session list`

The workspace's sessions, newest first. The audit list.

```text
--status
--all
```

### `dh session open`

Open a session and print its id.

```text
--agent
--wait
--no-wait
```

### `dh session statements`

A session's statements in execution order.

```text
<session_id>
--all
```

## `dh sql`

Run SQL and print the results.

```text
--query/-q
--file/-f
--stdin/-i
--no-wait
--timeout
--limit
--all
--agent
```

## `dh table`

Tables, their metadata and a sample of rows.

### `dh table create`

Create an Iceberg table.

```text
<table>
--column/-c
```

### `dh table drop`

Drop a table.

```text
<table>
```

### `dh table get`

One table's columns, partitioning and statistics.

```text
<table>
```

### `dh table health`

Maintenance findings for this table.

```text
<table>
```

### `dh table lineage`

What this table was built from.

```text
<table>
```

### `dh table list`

Tables in a schema.

```text
<schema>
```

### `dh table recount`

Recount the table's rows and refresh its stats.

```text
<table>
```

### `dh table sample`

A page of rows, for previewing without SQL.

```text
<table>
```

### `dh table snapshots`

The table's Iceberg snapshots.

```text
<table>
```

## `dh version`

The CLI's version, and the server's when one is reachable.

## `dh workspace`

Workspaces and their members.

### `dh workspace create`

Create a workspace.

```text
<slug>
--name
```

### `dh workspace delete`

Delete a workspace.

```text
<workspace>
```

### `dh workspace get`

One workspace, by slug or id. Defaults to the configured one.

```text
<workspace>
```

### `dh workspace list`

Workspaces you can see.

### `dh workspace member`

Workspace membership.

#### `dh workspace member add`

Add a member to the workspace.

```text
<user_id>
--role
```

#### `dh workspace member list`

Who belongs to the workspace, and in what role.

### `dh workspace update`

Rename or re-describe a workspace.

```text
<workspace>
--name
--description
```
