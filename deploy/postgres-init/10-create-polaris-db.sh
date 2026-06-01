#!/bin/sh
# Create a dedicated `polaris` database alongside `duckhaven` on first init.
# Polaris (relational-jdbc persistence) keeps its own schema here so its tables
# never collide with DuckHaven's Alembic-managed tables. Runs only on first
# boot (empty data dir), like all /docker-entrypoint-initdb.d scripts.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
	SELECT 'CREATE DATABASE polaris'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'polaris')\gexec
EOSQL
