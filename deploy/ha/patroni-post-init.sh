#!/bin/bash
# Patroni runs this once on the freshly-bootstrapped primary ($1 is a psql
# connection string to it). It creates the same roles/databases the bundled
# single-node postgres-init creates: the `duckhaven` login role + database, and
# the dedicated `polaris` database. They then replicate to the standbys
# automatically. Idempotent so a re-bootstrap is harmless.
set -e

psql "$1" <<-SQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'duckhaven') THEN
      CREATE ROLE duckhaven LOGIN PASSWORD '${DUCKHAVEN_DB_PASSWORD:-duckhaven}';
    END IF;
  END
  \$\$;
  SELECT 'CREATE DATABASE duckhaven OWNER duckhaven'
   WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'duckhaven')\gexec
  SELECT 'CREATE DATABASE polaris OWNER duckhaven'
   WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'polaris')\gexec
SQL
