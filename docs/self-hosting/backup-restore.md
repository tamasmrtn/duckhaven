# Backup and restore

DuckHaven's only durable state is the Postgres volume (`postgres_data`),
which holds users, workspaces, saved queries, audit log, agent registrations,
and the Polaris metastore (in the dedicated `polaris` database). The `secrets` volume holds `SECRET_KEY` and
`POSTGRES_PASSWORD`; you need both volumes to restore a working install.

Table data lives on your **storage backends** (bundled object storage, S3,
ADLS) — back those up via their own tooling.

## Backup

### Postgres

Dump from inside the running stack:

```bash
docker compose exec postgres pg_dump -U duckhaven duckhaven | gzip > duckhaven-$(date +%F).sql.gz
```

### Secrets

```bash
docker run --rm -v deploy_secrets:/secrets -v "$PWD":/out alpine \
    tar czf /out/duckhaven-secrets-$(date +%F).tgz -C /secrets .
```

(Replace `deploy_secrets` with the actual volume name from `docker volume ls`
if the compose project isn't named `deploy`.)

## Restore

### Postgres

Bring the stack up empty, then load the dump:

```bash
docker compose up -d postgres
gunzip -c duckhaven-2026-03-05.sql.gz | docker compose exec -T postgres psql -U duckhaven duckhaven
docker compose up -d
```

### Secrets

```bash
docker volume create deploy_secrets
docker run --rm -v deploy_secrets:/secrets -v "$PWD":/in alpine \
    tar xzf /in/duckhaven-secrets-2026-03-05.tgz -C /secrets
```

## Scheduled backups

A systemd timer wrapping `scripts/pg-backup.sh` ships under `deploy/systemd/`.
Drop the unit files into `/etc/systemd/system/` and enable:

```bash
sudo systemctl enable --now duckhaven-backup.timer
```

Backups land in `/var/duckhaven/backups` by default; override with
`DUCKHAVEN_BACKUP_DIR`.
