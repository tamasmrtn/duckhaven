#!/bin/sh
# Create the `duckhaven_system` database and lock down who may reach it.
#
# The API creates the tables and the read-only login agents use. The REVOKEs
# are load-bearing: without them that login would inherit PUBLIC's CONNECT on
# `duckhaven` and `polaris`, and CREATE on `duckhaven_system.public`.
#
# Runs only on an empty data dir; `scripts/enable-system-catalog.sh` covers
# existing deployments.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
	SELECT 'CREATE DATABASE duckhaven_system'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'duckhaven_system')\gexec

	REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC;
	REVOKE CONNECT ON DATABASE polaris FROM PUBLIC;
	REVOKE ALL ON DATABASE duckhaven_system FROM PUBLIC;
EOSQL

# Connected to `duckhaven_system` itself: schema privileges are per-database.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname duckhaven_system <<-'EOSQL'
	REVOKE ALL ON SCHEMA public FROM PUBLIC;
EOSQL
