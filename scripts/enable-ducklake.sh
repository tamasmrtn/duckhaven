#!/usr/bin/env bash
# Apply the DuckLake PostgreSQL setup to an EXISTING deployment.
#
# The idempotent equivalent of deploy/postgres-init/20-create-ducklake-db.sh,
# which initdb never runs on an existing data dir.
#
#   scripts/enable-ducklake.sh
set -euo pipefail

COMPOSE_FILE="$(dirname "$0")/../deploy/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" exec -T \
    postgres psql -v ON_ERROR_STOP=1 --username duckhaven --dbname duckhaven <<-'EOSQL'
	SELECT 'CREATE DATABASE ducklake'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake')\gexec

	REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC;
	REVOKE CONNECT ON DATABASE polaris FROM PUBLIC;
	REVOKE ALL ON DATABASE ducklake FROM PUBLIC;
EOSQL

docker compose -f "$COMPOSE_FILE" exec -T \
    postgres psql -v ON_ERROR_STOP=1 --username duckhaven --dbname ducklake <<-'EOSQL'
	REVOKE ALL ON SCHEMA public FROM PUBLIC;
EOSQL

echo
echo "DuckLake database is ready."
echo "Next: set DUCKLAKE_ENABLED=true in deploy/.env, 'docker compose up -d api',"
echo "then POST /api/admin/catalogs/ducklake/reconcile-roles if catalogs already exist."
