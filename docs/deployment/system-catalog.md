---
type: how-to
---

# Enable the system catalog database

DuckHaven keeps a read-only copy of its own records and of each catalog's metadata in a second Postgres database,
`duckhaven_system`, refreshed every 10 seconds.

People query the copy in SQL as the `duckhaven` catalog, each table with the permission it needs; see
[System catalog](../querying/system-catalog.md). It is kept apart from `duckhaven`, the database that holds users,
password hashes and session tokens, so that agents can attach it through a login that reaches nothing else. The API
creates that login, `dh_system_reader`, with a generated password; it can read the copied tables and nothing more.
Row-level security on each table returns rows only to a query that may read that table, and in `info_schema` only the
rows its principals can see.

A new install creates the database on first start. An install that predates it needs one command; see
[On an existing install](#on-an-existing-install).

## Before you begin

- **Applies to:** the single-host Compose stack. [Azure with Terraform](azure-terraform.md#system-catalog) creates
  the database itself. The [HA stack](high-availability.md#limitations) does not create the database. Agents that run
  on another host reach it at `SYSTEM_CATALOG_AGENT_HOST`; see the
  [configuration reference](../reference/configuration.md#system-catalog).
- **You need:** shell access to the control-plane host, in a checkout of the repository; see
  [Operator scripts](../reference/operator-scripts.md#requirements).
- **Enabled by:** `SYSTEM_CATALOG_ENABLED`, `true` by default; see the
  [configuration reference](../reference/configuration.md#system-catalog).

## What it holds

Eight schemas:

- `info_schema`: the schemas, tables, columns and DuckLake macros of every catalog, and every workspace's semantic
  models.
- `access`: the [access audit log](../access/audit-log.md), current access, users, workspaces, tokens, and which tables
  each query read and wrote.
- `query`: finished queries and SQL sessions.
- `lineage`: the lineage graph.
- `jobs`: schedules.
- `compute`: agents, their state changes and their minute-by-minute utilization.
- `storage`: table health at each maintenance scan, each maintenance apply, and the storage backends, without their
  configuration.
- `ai`: the tokens each assistant answer used, and each tool it called, without what was asked or answered.

Their tables, columns, and how long each keeps its rows, are in the
[system catalog reference](../reference/system-catalog.md). The copy keeps what DuckHaven's own pages let go: deleting a
workspace removes its queries from History but not from the copy, and agent utilization, which the monitoring page
keeps for a week, stays for `SYSTEM_CATALOG_RETENTION_DAYS`, as do the assistant's usage and tool calls after their
conversation is deleted.

The copy trails `duckhaven` by up to the publish interval. An audit event joins it once every transaction that was
writing before it on the same Postgres server has finished, so a transaction that runs for minutes holds the audit copy
back that long.

When an upgrade adds columns to `query.history`, the API copies the queries it already holds again, oldest first and
5,000 each publish interval, so that they gain them. New queries are copied as usual meanwhile; on an older row the new
columns stay empty until the copy reaches it.

`info_schema` is read from each catalog's own metadata. One replica at a time re-reads a table within a publish interval
or two of a statement or API call that changes it, and reads each catalog whole every
`SYSTEM_CATALOG_METADATA_REFRESH_S`, 15 minutes by default, two catalogs per publish interval. A whole read of an
Iceberg catalog costs Polaris one request per schema and one per table, eight at a time; of a DuckLake catalog, four
queries, and a fifth when it has macros. When the API first starts with the system catalog, a deployment of 100 catalogs
takes about 10 minutes to have every one listed.

## On a new install

Nothing to do. `deploy/postgres-init/30-create-system-db.sh` creates the database on first boot, and the API creates
its tables and the reader login when it starts.

## On an existing install

`/docker-entrypoint-initdb.d` only runs against an empty data directory, so an install that predates the system catalog
never sees the init script. From the repository checkout, run the same work against the running stack:

```bash
scripts/enable-system-catalog.sh
```

```text
CREATE DATABASE
REVOKE
REVOKE
REVOKE
REVOKE

System catalog database is ready. The API fills it within a minute;
no restart is needed.
```

It is safe to run twice. The API tries again every 10 seconds, so it finds the database without a restart, then
copies the whole audit log and the last `SYSTEM_CATALOG_QUERY_HISTORY_DAYS` of History.

The script also removes `CONNECT` on the `duckhaven` and `polaris` databases from `PUBLIC`, which Postgres grants by
default. Without that, the reader login could open the database that holds users and session tokens.

## Check that it worked

Count the rows in each table:

```bash
docker compose exec postgres psql -U duckhaven -d duckhaven_system -c \
  "SELECT (SELECT count(*) FROM access.audit) AS audit_events,
          (SELECT count(*) FROM access.grants) AS grants,
          (SELECT count(*) FROM query.history) AS queries;"
```

```text
 audit_events | grants | queries
--------------+--------+---------
            2 |      4 |       3
(1 row)
```

`grants` is never 0 once the API has filled it: every user has a global role.

## Watch how far behind it is

Each API replica exports `duckhaven_system_catalog_publish_lag_seconds`, the age of the oldest change still to be
copied, by table:

```bash
curl -s http://localhost:8000/api/metrics | grep '^duckhaven_system_catalog'
```

```text
duckhaven_system_catalog_publish_lag_seconds{table="access.audit"} 3.557579
duckhaven_system_catalog_publish_lag_seconds{table="access.grants"} 3.557579
duckhaven_system_catalog_publish_lag_seconds{table="query.history"} 3.557579
```

While copying keeps up, `access.audit` and `query.history` stay within the publish interval, and `access.grants`
climbs to 5 minutes between refreshes. The value is read from the database at each scrape, so it keeps rising if every
replica stops copying, and every replica reports the same value: alert on the `max`. The metric is absent while the
database cannot be reached. Two alerts cover both cases:

```yaml
- alert: DuckHavenSystemCatalogBehind
  expr: max by (table) (duckhaven_system_catalog_publish_lag_seconds) > 600
  for: 10m
  annotations:
    summary: "The system catalog's {{ $labels.table }} is more than 10 minutes behind"
- alert: DuckHavenSystemCatalogUnreachable
  expr: absent(duckhaven_system_catalog_publish_lag_seconds)
  for: 10m
  annotations:
    summary: "No API replica can read the system catalog database"
```

Add the second only where the system catalog is turned on; with it off, the metric is always absent.

## Back it up

[`scripts/pg-backup.sh`](../reference/operator-scripts.md#pg-backupsh) dumps `duckhaven_system` once it exists. Most of
it can be rebuilt: a restore without it is refilled from `duckhaven` within minutes. What cannot be rebuilt is the query
history of workspaces deleted since, which only the copy keeps.

After a restore, including into a new Postgres major version, the API carries on copying without help.

## Turning it off

Set `SYSTEM_CATALOG_ENABLED=false` in `deploy/.env` and restart the API:

```bash
docker compose up -d api
```

The API stops copying. The audit log and History are unaffected, and the database keeps what it holds; turning it back
on catches up from where copying stopped. To remove it, drop the database and the reader login:

```bash
docker compose exec postgres psql -U duckhaven -d duckhaven \
  -c "DROP DATABASE duckhaven_system;" -c "DROP ROLE dh_system_reader;"
```

## If it goes wrong

- **The API logs `System catalog unavailable (… database "duckhaven_system" does not exist)`.** The install predates
  the system catalog. Run `scripts/enable-system-catalog.sh` as in [On an existing install](#on-an-existing-install).
  The audit log and History work regardless.
- **The API logs `System catalog unavailable (… permission denied to create role)`, or `… to alter role`.** The login
  in `SYSTEM_CATALOG_DATABASE_URL` cannot create the reader login. The bundled stack's `duckhaven` login can; on a
  Postgres you run yourself, give the login `CREATEROLE`, or on a managed service, use its admin role.
- **On the HA stack, the API logs `System catalog unavailable (… Connect call failed …)`.** The HA stack creates no
  `duckhaven_system` database. Set `SYSTEM_CATALOG_ENABLED=false`.
- **The API logs `System catalog publish failed; retrying every 10.0s`.** Copying stopped on the error logged with it,
  and `duckhaven_system_catalog_publish_lag_seconds` rises until it clears. The message is logged once; the API logs
  `System catalog publishing recovered` when a later attempt succeeds.
- **A query on `duckhaven.access.audit` fails with `422` and `"error": "system_catalog_unavailable"`.** The system
  catalog is turned off, or the API replica that took the query cannot reach its database; the API log says which, as
  in the entries above.
- **A query on it fails with `422` and `"error": "agent_incompatible"`, saying the agent is too old.** The agent's
  image predates the release the API runs. Update the agent; see [Update DuckHaven](updating.md#agents).
- **A query on it fails with `IO Error: Unable to connect to Postgres at …`.** The agent cannot reach
  `SYSTEM_CATALOG_AGENT_HOST` on `SYSTEM_CATALOG_AGENT_PORT`. On the bundled stack that is `postgres:5432`; for an
  agent on another host, set them to an address it can reach and restart the API.
- **The API logs `Could not read catalog ⟨slug⟩ for the system catalog: …`.** `info_schema` keeps what it last read of
  that catalog, and the API tries again when the catalog is next due for a whole read, or on the next tick for a table a
  statement changed. The message is logged once. The catalog's metastore is unreachable, or, for an Iceberg catalog,
  the catalog is missing from Polaris.
- **Only `access.audit` falls behind, and nothing is logged.** A long transaction on the Postgres server is holding it
  back. Find it, oldest first:

    ```bash
    docker compose exec postgres psql -U duckhaven -d duckhaven -c \
      "SELECT pid, datname, now() - xact_start AS running, left(query, 60) AS query
       FROM pg_stat_activity WHERE backend_xid IS NOT NULL ORDER BY xact_start LIMIT 5;"
    ```

## Related

- [System catalog](../querying/system-catalog.md): querying the copy in SQL.
- [Access audit log](../access/audit-log.md): what is recorded, and how long it is kept.
- [Backup and restore](backup-restore.md): the other databases, and restoring them.
- [Metric reference](../reference/metrics.md): every metric DuckHaven exports.
