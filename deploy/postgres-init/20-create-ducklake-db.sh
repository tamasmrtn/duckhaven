#!/bin/sh
# Create the `ducklake` database and lock down who may reach it.
#
# DuckLake agents connect directly to the catalog database -- no credential
# vendor sits in front of it like Polaris does for Iceberg -- so they need a
# PostgreSQL login. Each catalog gets its own, created by the API when the
# catalog is provisioned; there is no shared role and no shared password.
#
# The REVOKEs are load-bearing. PostgreSQL grants CONNECT on every database to
# PUBLIC by default, and CREATE on the `public` schema too on versions before
# 15. Without these, a per-catalog role would inherit the right to open
# `duckhaven` (users, password hashes, session tokens) and to create objects in
# `ducklake.public` -- which would make per-catalog roles decorative.
#
# Runs only on an empty data dir, like all initdb scripts;
# `scripts/enable-ducklake.sh` applies the same setup to an existing deployment.
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
