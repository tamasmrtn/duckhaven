# Backup and restore

DuckHaven's only durable state is the Postgres volume (`postgres_data`),
which holds users, workspaces, saved queries, audit log, agent registrations,
and the Polaris metastore (in the dedicated `polaris` database). The `secrets` volume holds `SECRET_KEY` and
`POSTGRES_PASSWORD`; you need both volumes to restore a working install.

Table data lives on your **storage backends** (bundled object storage, S3,
ADLS) — back those up via their own tooling.

## Backup

### Postgres

Both databases live in the same Postgres instance and are both required to
restore a working install: `duckhaven` (users, workspaces, saved queries,
audit log, agent registrations) and `polaris` (the Iceberg metastore). Dump
each from inside the running stack:

```bash
docker compose exec postgres pg_dump -U duckhaven duckhaven | gzip > duckhaven-$(date +%F).sql.gz
docker compose exec postgres pg_dump -U duckhaven polaris | gzip > polaris-$(date +%F).sql.gz
```

### Secrets

```bash
docker run --rm -v deploy_secrets:/secrets -v "$PWD":/out alpine \
    tar czf /out/duckhaven-secrets-$(date +%F).tgz -C /secrets .
```

(Replace `deploy_secrets` with the actual volume name from `docker volume ls`
if the compose project isn't named `deploy`.)

## Restore

### Postgres (restore)

Bring the stack up empty, then load both dumps:

```bash
docker compose up -d postgres
gunzip -c duckhaven-2026-03-05.sql.gz | docker compose exec -T postgres psql -U duckhaven duckhaven
gunzip -c polaris-2026-03-05.sql.gz | docker compose exec -T postgres psql -U duckhaven polaris
docker compose up -d
```

### Secrets (restore)

```bash
docker volume create deploy_secrets
docker run --rm -v deploy_secrets:/secrets -v "$PWD":/in alpine \
    tar xzf /in/duckhaven-secrets-2026-03-05.tgz -C /secrets
```

## Postgres major-version upgrade

Docker's official `postgres` image (and Zalando's `spilo` image, used by the
[HA topology](high-availability.md)) cannot start against a data directory
initialized by a different major version — bumping the image tag in place
will crash-loop the container. Upgrading requires a dump/restore, not a tag
swap. This is also the procedure to repeat for the next major bump.

### Base stack

```bash
# 1. Back up both databases from the running (old-version) stack.
docker compose exec postgres pg_dump -U duckhaven duckhaven | gzip > duckhaven-preupgrade.sql.gz
docker compose exec postgres pg_dump -U duckhaven polaris | gzip > polaris-preupgrade.sql.gz

# 2. Stop the stack and drop the old data volume (the new major version's
#    postgres-init will recreate an empty `polaris` database on first boot).
docker compose down
docker volume rm deploy_postgres_data   # match the actual volume name from `docker volume ls`

# 3. Pull the new image (already pinned in docker-compose.yml) and bring
#    Postgres up alone.
docker compose up -d postgres

# 4. Restore both databases.
gunzip -c duckhaven-preupgrade.sql.gz | docker compose exec -T postgres psql -U duckhaven duckhaven
gunzip -c polaris-preupgrade.sql.gz | docker compose exec -T postgres psql -U duckhaven polaris

# 5. Bring up the rest of the stack. Alembic migrations and the Polaris
#    bootstrap are idempotent against already-migrated/already-bootstrapped
#    data, so this is a no-op beyond starting the services.
docker compose up -d
```

### HA stack

Same idea, run against the Patroni cluster:

```bash
# 1. Back up both databases via HAProxy (the current Patroni leader).
docker compose -f deploy/docker-compose.ha.yml exec pg-haproxy \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD pg_dump -h 127.0.0.1 -U duckhaven duckhaven' \
    | gzip > duckhaven-preupgrade.sql.gz
docker compose -f deploy/docker-compose.ha.yml exec pg-haproxy \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD pg_dump -h 127.0.0.1 -U duckhaven polaris' \
    | gzip > polaris-preupgrade.sql.gz

# 2. Stop the Patroni cluster and drop both node volumes.
docker compose -f deploy/docker-compose.ha.yml down
docker volume rm deploy_patroni1_data deploy_patroni2_data

# 3. Bring the cluster up on the new image (already pinned in
#    docker-compose.ha.yml). A fresh bootstrap re-runs patroni-post-init.sh,
#    which idempotently recreates the empty `duckhaven`/`polaris` databases.
docker compose -f deploy/docker-compose.ha.yml up -d etcd patroni-1 patroni-2 pg-haproxy

# 4. Confirm a leader was elected before restoring.
docker compose -f deploy/docker-compose.ha.yml exec patroni-1 patronictl list

# 5. Restore both databases through HAProxy (routes to the current leader).
gunzip -c duckhaven-preupgrade.sql.gz | docker compose -f deploy/docker-compose.ha.yml exec -T pg-haproxy \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD psql -h 127.0.0.1 -U duckhaven duckhaven'
gunzip -c polaris-preupgrade.sql.gz | docker compose -f deploy/docker-compose.ha.yml exec -T pg-haproxy \
    sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD psql -h 127.0.0.1 -U duckhaven polaris'

# 6. Bring up the rest of the stack.
docker compose -f deploy/docker-compose.ha.yml up -d
```

!!! note
    PostgreSQL 18 enables data checksums by default for a fresh `initdb`.
    That's irrelevant to the dump/restore path above (each side gets its own
    independent fresh cluster) — it only matters if you instead reach for
    binary `pg_upgrade`, which requires matching checksum settings between
    the old and new clusters and isn't the approach documented here.

## Scheduled backups

A systemd timer wrapping `scripts/pg-backup.sh` ships under `deploy/systemd/`.
Drop the unit files into `/etc/systemd/system/` and enable:

```bash
sudo systemctl enable --now duckhaven-backup.timer
```

Backups land in `/var/duckhaven/backups` by default; override with
`DUCKHAVEN_BACKUP_DIR`.
