#!/usr/bin/env bash
# Apply the DuckLake Postgres setup to an EXISTING deployment.
#
# deploy/postgres-init/20-create-ducklake-db.sh does this on a fresh install, but
# /docker-entrypoint-initdb.d only runs against an empty data dir — so an
# installation that predates DuckLake never sees it. This script is the same
# work, idempotent, against a running stack.
#
# It creates the `ducklake` database and the restricted `ducklake_agent` role,
# and revokes PUBLIC's default CONNECT on `duckhaven` and `polaris` so that role
# cannot reach the control plane's own database.
#
#   DUCKLAKE_AGENT_PASSWORD=... scripts/enable-ducklake.sh
#
# Afterwards, set DUCKLAKE_ENABLED=true (and the same DUCKLAKE_AGENT_PASSWORD) in
# deploy/.env and restart the api service.
set -euo pipefail

COMPOSE_FILE="$(dirname "$0")/../deploy/docker-compose.yml"
AGENT_PASSWORD="${DUCKLAKE_AGENT_PASSWORD:-}"

if [ -z "$AGENT_PASSWORD" ]; then
    echo "DUCKLAKE_AGENT_PASSWORD is not set." >&2
    echo "Choose one, set it here and in deploy/.env, and re-run." >&2
    exit 1
fi

docker compose -f "$COMPOSE_FILE" exec -T \
    postgres psql -v ON_ERROR_STOP=1 --username duckhaven --dbname duckhaven \
    -v agent_password="$AGENT_PASSWORD" <<-'EOSQL'
	SELECT 'CREATE DATABASE ducklake'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake')\gexec

	REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC;
	REVOKE CONNECT ON DATABASE polaris FROM PUBLIC;

	SELECT format('CREATE ROLE ducklake_agent LOGIN PASSWORD %L', :'agent_password')
	WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ducklake_agent')\gexec

	ALTER ROLE ducklake_agent WITH PASSWORD :'agent_password';
	GRANT CONNECT ON DATABASE ducklake TO ducklake_agent;
EOSQL

echo
echo "DuckLake database and role are ready."
echo "Next: set DUCKLAKE_ENABLED=true and DUCKLAKE_AGENT_PASSWORD in deploy/.env,"
echo "then 'docker compose up -d api' to pick them up."
