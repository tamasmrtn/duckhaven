# Operator scripts

DuckHaven ships a few small helper scripts under `scripts/` for operators. There is no separate `duckhaven` CLI — these
are the supported command-line helpers.

## `gen-token.sh` — mint an agent bootstrap token

Generates a one-time [bootstrap token](../deployment/add-agent.md) via the API, as an alternative to the admin UI.

```bash
SESSION_COOKIE=<your session cookie> ./scripts/gen-token.sh
```

`API_URL` defaults to `http://localhost:8000`. The token is printed as JSON.

## `pg-backup.sh` — back up Postgres

Dumps the DuckHaven database (app state plus the Polaris metastore) to a timestamped, gzipped file.

```bash
DUCKHAVEN_BACKUP_DIR=/mnt/nas/duckhaven ./scripts/pg-backup.sh
```

`DUCKHAVEN_BACKUP_DIR` defaults to `/var/duckhaven/backups`; point it at a separate disk or NAS in production. A systemd
timer wrapping this script ships under `deploy/systemd/` — see [Backup & restore](../deployment/backup-restore.md).

## `wait-for-stack.sh` — wait for healthy containers

Blocks until the `api` and `agent` containers report healthy. Used by the end-to-end CI job and handy locally after
`make compose-up`.

```bash
./scripts/wait-for-stack.sh
```
