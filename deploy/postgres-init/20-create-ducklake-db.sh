#!/bin/sh
# Create the `ducklake` database and lock down who may reach it.
#
# Per-catalog agent logins are created by the API. The REVOKEs are
# load-bearing: without them those roles would inherit PUBLIC's CONNECT on
# `duckhaven` and CREATE on `ducklake.public`.
#
# Runs only on an empty data dir; `scripts/enable-ducklake.sh` covers existing
# deployments.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
	SELECT 'CREATE DATABASE ducklake'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake')\gexec

	REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC;
	REVOKE CONNECT ON DATABASE polaris FROM PUBLIC;
	REVOKE ALL ON DATABASE ducklake FROM PUBLIC;
EOSQL

# Connected to `ducklake` itself: schema privileges are per-database.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname ducklake <<-'EOSQL'
	REVOKE ALL ON SCHEMA public FROM PUBLIC;
EOSQL
