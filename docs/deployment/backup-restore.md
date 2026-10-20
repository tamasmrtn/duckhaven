---
type: how-to
---

# Backup and restore

Back up DuckHaven's three Postgres databases and its secret key, restore them, and upgrade Postgres to a new major
version without losing data.

DuckHaven's durable state is the `postgres_data` volume and the `api_data` volume. Postgres holds three databases, and
a working install needs all of them:

| Database | Holds |
|---|---|
| `duckhaven` | Users, workspaces, saved queries, history and audit log, agent registrations |
| `polaris` | The Iceberg metastore |
| `ducklake` | The only copy of every DuckLake table's schema, snapshots and file list |

`api_data` holds the generated `SECRET_KEY`. Losing it invalidates every session and token, so back it up with the
databases. Table data lives on your [storage backends](../data/storage-backends.md); back those up with their own
tools.

## Before you begin

- **You need:** shell access to the host that runs the compose stack, in its `deploy/` directory.
- **Applies to:** the single-host compose stack. On the [HA stack](high-availability.md), run `pg_dump` and `psql`
  from a Patroni node through `pg-haproxy`, as in [the HA upgrade](#ha-stack) below.

## Back up the databases

From the stack's directory, dump each database:

```bash
docker compose exec -T postgres pg_dump -U duckhaven duckhaven | gzip > duckhaven-$(date +%F).sql.gz
docker compose exec -T postgres pg_dump -U duckhaven polaris | gzip > polaris-$(date +%F).sql.gz
docker compose exec -T postgres pg_dump -U duckhaven ducklake | gzip > ducklake-$(date +%F).sql.gz
```

Copy the files off the host. A backup on the same disk as the data does not survive that disk.

## Back up the secret key

`SECRET_KEY` lives in the `api_data` volume, so archive the volume:

```bash
docker run --rm -v deploy_api_data:/data -v "$PWD":/out alpine \
    tar czf /out/duckhaven-api-data-$(date +%F).tgz -C /data .
```

Replace `deploy_api_data` with the name `docker volume ls` shows if your compose project is not called `deploy`. There
is no separate secrets volume; naming one creates an empty volume and archives nothing.

## Back up every night

A systemd timer that runs [`scripts/pg-backup.sh`](../reference/operator-scripts.md#pg-backupsh) at 02:30 ships under
`deploy/systemd/`. The script needs a checkout of the repository on the host, at `/opt/duckhaven` for the units as
shipped.

```bash
sudo cp deploy/systemd/duckhaven-backup.{service,timer} /etc/systemd/system/
# Edit WorkingDirectory and DUCKHAVEN_BACKUP_DIR in the .service first.
sudo systemctl enable --now duckhaven-backup.timer
systemctl list-timers duckhaven-backup.timer
```

Point `DUCKHAVEN_BACKUP_DIR` at a second disk or a NAS mount; it defaults to `/var/duckhaven/backups`.

## Restore

1. Restore object storage first, if you are restoring it too; see
   [Restoring DuckLake catalogs](#restoring-ducklake-catalogs).
2. Restore the secret key into a new volume:

    ```bash
    docker volume create deploy_api_data
    docker run --rm -v deploy_api_data:/data -v "$PWD":/in alpine \
        tar xzf /in/duckhaven-api-data-2026-03-05.tgz -C /data
    ```

3. Start Postgres alone, and load the three dumps into the databases of the same names:

    ```bash
    docker compose up -d postgres
    gunzip -c duckhaven-2026-03-05.sql.gz | docker compose exec -T postgres psql -U duckhaven duckhaven
    gunzip -c polaris-2026-03-05.sql.gz | docker compose exec -T postgres psql -U duckhaven polaris
    gunzip -c ducklake-2026-03-05.sql.gz | docker compose exec -T postgres psql -U duckhaven ducklake
    ```

4. Start the rest of the stack: `docker compose up -d`.

Each DuckLake catalog's login role (`dl_⟨catalog⟩`) is a Postgres role, which `pg_dump` does not include. It is
recreated when the catalog is next used, or for every catalog at once with the `reconcile-roles` call in
[Enable DuckLake](ducklake.md).

### Restoring DuckLake catalogs

An Iceberg table cannot get out of step with its metadata, which sits beside the data in object storage. A DuckLake
catalog's metadata is in the `ducklake` database and its data in object storage, two systems that can be restored to
different moments. **Restore object storage first, then the `ducklake` database from the newest dump no newer than
the storage snapshot.** The two ways to get it wrong differ:

- **Catalog older than storage:** the catalog does not know the newest Parquet files. They cost storage and nothing
  else, and queries return the older, consistent state. Aim for this.
- **Catalog newer than storage:** the catalog names files the restored storage lacks, and queries on those tables fail
  on a missing file. This is the case that looks like corruption.

After restoring, run [`scripts/ducklake-check.py`](../reference/operator-scripts.md#ducklake-checkpy), which reports
both directions for every DuckLake catalog and deletes nothing.

!!! warning "The `ducklake` database is not optional"
    A DuckLake table's schema, snapshots and file list exist only in the `ducklake` database. A backup of object storage
    without it leaves Parquet nothing can read; unlike Iceberg, there is no metadata in the bucket to rebuild from.

## Data on each backend

- **`s3` and `adls_gen2`:** durability is your cloud provider's. Turn on S3 versioning and replication, or ADLS soft
  delete and redundancy, to match your target.
- **`object_store`, the bundled store:** it has no off-host copy, and the storage admin page says so. Back up its
  volume, or mirror the bucket to another store.

## Upgrade Postgres to a new major version

Postgres cannot start on a data directory from another major version, so bumping the image tag in place makes the
container restart in a loop. A major upgrade is a dump and restore.

### Base stack

```bash
# 1. Dump all three databases from the running, old-version stack.
docker compose exec -T postgres pg_dump -U duckhaven duckhaven | gzip > duckhaven-preupgrade.sql.gz
docker compose exec -T postgres pg_dump -U duckhaven polaris | gzip > polaris-preupgrade.sql.gz
docker compose exec -T postgres pg_dump -U duckhaven ducklake | gzip > ducklake-preupgrade.sql.gz

# 2. Stop the stack and remove the old data volume. The new Postgres creates
#    empty `polaris` and `ducklake` databases on first start.
docker compose down
docker volume rm deploy_postgres_data   # the name `docker volume ls` shows

# 3. Start Postgres on the new image alone.
docker compose up -d postgres

# 4. Restore all three databases.
gunzip -c duckhaven-preupgrade.sql.gz | docker compose exec -T postgres psql -U duckhaven duckhaven
gunzip -c polaris-preupgrade.sql.gz | docker compose exec -T postgres psql -U duckhaven polaris
gunzip -c ducklake-preupgrade.sql.gz | docker compose exec -T postgres psql -U duckhaven ducklake

# 5. Start the rest. Migrations and the Polaris bootstrap find their work done.
docker compose up -d
```

### HA stack

The same steps, run against the Patroni cluster through `pg-haproxy`, which routes to the current leader. The HA
stack does not create a `ducklake` database (see [High availability](high-availability.md#limitations)), so there are
two databases to move:

```bash
# 1. Dump both databases from a Patroni node; the HAProxy image has no Postgres client.
docker compose -f deploy/docker-compose.ha.yml exec patroni-1 \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD pg_dump -h pg-haproxy -U duckhaven duckhaven' \
    | gzip > duckhaven-preupgrade.sql.gz
docker compose -f deploy/docker-compose.ha.yml exec patroni-1 \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD pg_dump -h pg-haproxy -U duckhaven polaris' \
    | gzip > polaris-preupgrade.sql.gz

# 2. Stop the cluster and remove both node volumes.
docker compose -f deploy/docker-compose.ha.yml down
docker volume rm deploy_patroni1_data deploy_patroni2_data

# 3. Start the cluster on the new image; its first start recreates the empty databases.
docker compose -f deploy/docker-compose.ha.yml up -d etcd patroni-1 patroni-2 pg-haproxy

# 4. Confirm a leader was elected.
docker compose -f deploy/docker-compose.ha.yml exec patroni-1 patronictl list

# 5. Restore both databases.
gunzip -c duckhaven-preupgrade.sql.gz | docker compose -f deploy/docker-compose.ha.yml exec -T patroni-1 \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD psql -h pg-haproxy -U duckhaven duckhaven'
gunzip -c polaris-preupgrade.sql.gz | docker compose -f deploy/docker-compose.ha.yml exec -T patroni-1 \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD psql -h pg-haproxy -U duckhaven polaris'

# 6. Start the rest of the stack.
docker compose -f deploy/docker-compose.ha.yml up -d
```

## Check that it worked

Sign in, open a workspace, and run a query on a table from each catalog kind you use. For DuckLake catalogs, also run
`scripts/ducklake-check.py`, which exits non-zero if a catalog names a file storage does not have.

## If it goes wrong

### Postgres restarts in a loop after an image update

The data directory is from an older major version. Restore the old image tag, then follow
[Upgrade Postgres to a new major version](#upgrade-postgres-to-a-new-major-version).

### Everyone is signed out after a restore, and tokens stop working

The `api_data` volume was not restored, so a new `SECRET_KEY` was generated. Restore the volume from the same backup
and restart the API. Tokens issued under the new key will stop working.

### DuckLake queries fail on a missing file after a restore

The `ducklake` database is newer than the restored storage. Restore an older catalog dump, no newer than the storage
snapshot; see [Restoring DuckLake catalogs](#restoring-ducklake-catalogs).

## Related

- [Operator scripts](../reference/operator-scripts.md) — `pg-backup.sh` and `ducklake-check.py`.
- [Enable DuckLake](ducklake.md) — the `ducklake` database and its roles.
- [High availability](high-availability.md) — the Patroni cluster.
