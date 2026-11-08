#!/usr/bin/env bash
set -euo pipefail
# A dump holds every row and stored credential in the database, so it is
# readable by its owner only, whatever umask the caller runs with.
umask 077

# Point this at a second disk / NAS mount in production (G-D18-b).
BACKUP_DIR="${DUCKHAVEN_BACKUP_DIR:-/var/duckhaven/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
COMPOSE_FILE="$(dirname "$0")/../deploy/docker-compose.yml"

mkdir -p "$BACKUP_DIR"
# Also closes off dumps an earlier run left readable. A mount that does not
# keep modes refuses this; say so rather than skip the backup.
chmod 0700 "$BACKUP_DIR" ||
    echo "warning: could not restrict $BACKUP_DIR to its owner; check the mount's permissions" >&2

# All are required to restore a working install: `duckhaven`, `polaris` (the
# Iceberg metastore) and, when it exists, `ducklake` (DuckLake catalog metadata).
databases="duckhaven polaris"
if docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U duckhaven -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname = 'ducklake'" | grep -q 1; then
    databases="$databases ducklake"
fi

# DuckLake metadata and data can be restored to different moments; warn here.
case " $databases " in
    *" ducklake "*)
        echo "note: DuckLake is enabled. Snapshot object storage at the same point"
        echo "      in time as this dump, and restore storage first. See"
        echo "      docs/deployment/backup-restore.md and scripts/ducklake-check.py."
        ;;
esac

for db in $databases; do
    backup_file="${BACKUP_DIR}/${db}_${TIMESTAMP}.sql.gz"
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
        pg_dump -U duckhaven "$db" | gzip > "$backup_file"
    echo "Backup written to $backup_file"
done
