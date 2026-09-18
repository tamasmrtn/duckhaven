#!/bin/sh
# Create the `ducklake` database and the restricted role agents use to reach it.
#
# DuckLake agents connect directly to the catalog database — no credential
# vendor sits in front of it like Polaris does for Iceberg — so they need a
# Postgres login. The REVOKEs are load-bearing: Postgres grants CONNECT to
# PUBLIC by default, which would let the role reach `duckhaven` (users, password
# hashes, session tokens). Per-catalog schema privileges are granted by the API
# at catalog creation. Runs only on an empty data dir, like all initdb scripts;
# `scripts/enable-ducklake.sh` applies the same setup to an existing deployment.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
	-v agent_password="${DUCKLAKE_AGENT_PASSWORD:-ducklake}" <<-'EOSQL'
	SELECT 'CREATE DATABASE ducklake'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake')\gexec

	REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC;
	REVOKE CONNECT ON DATABASE polaris FROM PUBLIC;

	SELECT format('CREATE ROLE ducklake_agent LOGIN PASSWORD %L', :'agent_password')
	WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ducklake_agent')\gexec

	ALTER ROLE ducklake_agent WITH PASSWORD :'agent_password';
	GRANT CONNECT ON DATABASE ducklake TO ducklake_agent;
EOSQL
