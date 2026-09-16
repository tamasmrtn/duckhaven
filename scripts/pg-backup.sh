#!/usr/bin/env bash
set -euo pipefail

# Point this at a second disk / NAS mount in production (G-D18-b).
BACKUP_DIR="${DUCKHAVEN_BACKUP_DIR:-/var/duckhaven/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
COMPOSE_FILE="$(dirname "$0")/../deploy/docker-compose.yml"

mkdir -p "$BACKUP_DIR"

# These databases live in the same Postgres instance and are each required to
# restore a working install: `duckhaven` (users, workspaces, saved queries,
# audit log, agent registrations), `polaris` (the Iceberg metastore) and, when
# DuckLake is enabled, `ducklake` (the DuckLake catalog metadata).
#
# `ducklake` is skipped when absent, so this keeps working on an installation
# that has never enabled DuckLake. When it IS present it is not optional: a
# DuckLake table's schema, snapshots and file list live only there, so restoring
# without it leaves the Parquet in object storage with nothing able to read it.
databases="duckhaven polaris"
if docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U duckhaven -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname = 'ducklake'" | grep -q 1; then
    databases="$databases ducklake"
fi

for db in $databases; do
    backup_file="${BACKUP_DIR}/${db}_${TIMESTAMP}.sql.gz"
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
        pg_dump -U duckhaven "$db" | gzip > "$backup_file"
    echo "Backup written to $backup_file"
done
