#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/var/duckhaven/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/duckhaven_${TIMESTAMP}.sql.gz"
COMPOSE_FILE="$(dirname "$0")/../deploy/docker-compose.yml"

mkdir -p "$BACKUP_DIR"
docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -U duckhaven duckhaven | gzip > "$BACKUP_FILE"

echo "Backup written to $BACKUP_FILE"
