#!/usr/bin/env bash
# Apply the DuckLake PostgreSQL setup to an EXISTING deployment.
#
# /docker-entrypoint-initdb.d only runs against an empty data dir, so an
# installation predating DuckLake never sees 20-create-ducklake-db.sh. This is
# the same idempotent work against a running stack: create the `ducklake`
# database and revoke the default PUBLIC privileges that would otherwise let any
# role reach `duckhaven` or create objects in `ducklake.public`.
#
#   scripts/enable-ducklake.sh
#
# There is no shared agent role and no password to choose: each catalog gets its
# own login, created by the API when the catalog is provisioned. After enabling
# DuckLake on a deployment that already has catalogs, run
#   POST /api/admin/catalogs/ducklake/reconcile-roles
# to create the roles for them rather than waiting for each to be browsed.
#
# Afterwards, set DUCKLAKE_ENABLED=true in deploy/.env and restart the api.
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
