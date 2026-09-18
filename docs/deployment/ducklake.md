# Enable DuckLake

[DuckLake](../concepts/ducklake.md) is a second [catalog](../concepts/catalogs.md) kind, off by default. Turning it on
adds nothing to the stack: it uses the PostgreSQL and object storage DuckHaven already runs.

Read [the trade-off](../concepts/ducklake.md#the-trade-off-stated-plainly) before enabling it. A DuckLake table can be
read by DuckDB and nothing else.

## What it needs

| Requirement | Why |
|---|---|
| A `ducklake` database | Holds one schema per catalog, containing that catalog's `ducklake_*` tables |
| A `ducklake_agent` role | The login agents authenticate with; granted `CONNECT` on `ducklake` only |
| `postgres` on the agent network | Agents connect to the catalog database directly — there is no vendor in front of it |
| An agent image with `ducklake` + `postgres` | Baked in; an agent on an isolated network cannot download them at runtime |

## On a new install

Nothing to do beyond setting the flag. `deploy/postgres-init/20-create-ducklake-db.sh` creates the database and the
role on first boot.

In `deploy/.env`:

```bash
DUCKLAKE_ENABLED=true
DUCKLAKE_AGENT_PASSWORD=<choose a strong password>
```

Then `docker compose up -d`.

## On an existing install

`/docker-entrypoint-initdb.d` only runs against an empty data directory, so an installation that predates DuckLake
never sees the init script. Run the same work against the running stack:

```bash
DUCKLAKE_AGENT_PASSWORD=<choose a strong password> scripts/enable-ducklake.sh
```

It is idempotent. Then set `DUCKLAKE_ENABLED=true` and the **same** `DUCKLAKE_AGENT_PASSWORD` in `deploy/.env`, and
restart the API and Postgres so the new network membership takes effect:

```bash
docker compose up -d postgres api
```

Agents need an image containing the `ducklake` and `postgres` extensions. An older agent is not broken by this — it
simply will not be offered for a DuckLake catalog, and the error says which extension it lacks.

## Why the isolation matters

This is the part worth understanding rather than copying.

An Iceberg catalog's agent never holds a credential: Polaris vends short-lived storage credentials when the agent
attaches. DuckLake has no such vendor — the agent *is* the catalog client, so it needs a PostgreSQL login, and
`postgres` has to join the otherwise-isolated `duckhaven_internal` network for it to connect.

What keeps that acceptable is the role. `ducklake_agent` has `CONNECT` on the `ducklake` database and nothing else, and
PostgreSQL's default `CONNECT` grant to `PUBLIC` is revoked on `duckhaven` and `polaris`. Without those revokes the
role would reach the database holding users, password hashes and session tokens.

!!! warning "Do not widen the agent role"
    Granting `ducklake_agent` access to the `duckhaven` database, or configuring agents with the owner credential,
    undoes the isolation the agent network exists for. The API connects as the owner; agents must not.

Storage credentials are minted by the API per query and never written to agent disk. For external S3 they are STS
credentials expiring within the hour; for ADLS Gen 2, a user-delegation SAS. For the bundled object store they are the
store's static key, scoped to the catalog's own prefix — weaker than what Polaris vends, and worth knowing if that
matters to you.

!!! note "How far each backend has been exercised"
    The bundled object store and external S3 are verified end to end — external S3 against a real STS `AssumeRole`,
    including writing Parquet and purging it on drop. **ADLS Gen 2 is implemented but has not been run against a real
    storage account**, because there is no local substitute (Azurite has no Entra, so no user-delegation SAS). Treat a
    DuckLake catalog on ADLS as untested until you have tried it on a non-production account.

## Back up the catalog database

A DuckLake table's schema, snapshots and file list live **only** in the `ducklake` database. A backup without it leaves
Parquet in object storage that nothing can read.

`scripts/pg-backup.sh` picks the database up automatically once it exists. If you back up by other means, add it.

## A DuckLake-only deployment

If every catalog is DuckLake, Polaris is not needed at all. Bring the stack up with the override that removes it:

```bash
docker compose -f docker-compose.yml -f docker-compose.ducklake-only.yml up -d
```

That drops `polaris` and `polaris-bootstrap`, leaving eight services instead of ten.

!!! warning "One-way for existing catalogs"
    Do this only on a deployment with no Iceberg catalogs. Removing Polaris makes any existing Iceberg catalog
    unreadable until it is brought back.

## Turning it off

Set `DUCKLAKE_ENABLED=false` and restart the API. Existing DuckLake catalogs stop being reachable but are not deleted;
their metadata schemas and Parquet stay where they are, and re-enabling the flag restores access. Drop the catalogs
first if you want the data gone — dropping a catalog purges its object-storage prefix.

## Related

- [DuckLake](../concepts/ducklake.md) — what it is and what it trades away.
- [Create a DuckLake catalog](../guides/create-a-ducklake-catalog.md) — the walkthrough.
- [Configure storage](storage.md) — the orthogonal axis.
