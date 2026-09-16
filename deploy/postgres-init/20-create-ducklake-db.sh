#!/bin/sh
# Create the `ducklake` database and the restricted role agents use to reach it.
#
# DuckLake keeps its catalog in SQL tables, and the client — a DuckDB agent —
# connects to that database directly; there is no credential vendor in front of
# it the way Polaris sits in front of Iceberg storage. So agents need a Postgres
# login, and `postgres` has to join the otherwise-isolated `duckhaven_internal`
# network for them to reach it.
#
# Both of those are only acceptable because of what this script does: a
# `ducklake_agent` role that can connect to `ducklake` and nothing else. Postgres
# grants CONNECT to PUBLIC on every database by default, so the REVOKEs are the
# load-bearing part — without them the role reaches `duckhaven`, which holds
# users, password hashes and session tokens.
#
# Per-catalog schema privileges are granted by the API at catalog creation, not
# here. Runs only on first boot (empty data dir), like all
# /docker-entrypoint-initdb.d scripts; `scripts/enable-ducklake.sh` applies the
# same thing to an existing deployment.
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
