#!/usr/bin/env bash
# Apply the system catalog's PostgreSQL setup to an EXISTING deployment.
#
# The idempotent equivalent of deploy/postgres-init/30-create-system-db.sh,
# which initdb never runs on an existing data dir.
#
#   scripts/enable-system-catalog.sh
set -euo pipefail

COMPOSE_FILE="$(dirname "$0")/../deploy/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" exec -T \
    postgres psql -v ON_ERROR_STOP=1 --username duckhaven --dbname duckhaven <<-'EOSQL'
	SELECT 'CREATE DATABASE duckhaven_system'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'duckhaven_system')\gexec

	REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC;
	REVOKE CONNECT ON DATABASE polaris FROM PUBLIC;
	REVOKE ALL ON DATABASE duckhaven_system FROM PUBLIC;
EOSQL

docker compose -f "$COMPOSE_FILE" exec -T \
    postgres psql -v ON_ERROR_STOP=1 --username duckhaven --dbname duckhaven_system <<-'EOSQL'
	REVOKE ALL ON SCHEMA public FROM PUBLIC;
EOSQL

echo
echo "System catalog database is ready. The API fills it within a minute;"
echo "no restart is needed."
