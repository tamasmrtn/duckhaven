#!/usr/bin/env bash
set -euo pipefail

# Point this at a second disk / NAS mount in production (G-D18-b).
BACKUP_DIR="${DUCKHAVEN_BACKUP_DIR:-/var/duckhaven/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
COMPOSE_FILE="$(dirname "$0")/../deploy/docker-compose.yml"

mkdir -p "$BACKUP_DIR"

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
        echo "      docs/operations/runbook.md and scripts/ducklake-check.py."
        ;;
esac

for db in $databases; do
    backup_file="${BACKUP_DIR}/${db}_${TIMESTAMP}.sql.gz"
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
        pg_dump -U duckhaven "$db" | gzip > "$backup_file"
    echo "Backup written to $backup_file"
done
