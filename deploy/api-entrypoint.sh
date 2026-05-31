#!/bin/sh
# Read persisted secrets and construct env vars before launching the API. The
# init-secrets service (see deploy/docker-compose.yml) guarantees the files
# exist before this runs.
set -eu

SECRETS_DIR="${SECRETS_DIR:-/var/duckhaven/secrets}"

read_secret() {
    path="$SECRETS_DIR/$1"
    if [ ! -s "$path" ]; then
        echo "entrypoint: missing secret file: $path" >&2
        exit 1
    fi
    cat "$path"
}

SECRET_KEY="$(read_secret secret_key)"
export SECRET_KEY

POSTGRES_PASSWORD="$(read_secret postgres_password)"
DB_USER="${POSTGRES_USER:-duckhaven}"
DB_HOST="${POSTGRES_HOST:-postgres}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_NAME="${POSTGRES_DB:-duckhaven}"
DATABASE_URL="postgresql+asyncpg://${DB_USER}:${POSTGRES_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
export DATABASE_URL

# Apply pending migrations before the app starts. Only runs inside the built
# image where /app/alembic.ini exists; in unit tests of this script the file
# is absent and the step is skipped.
if [ -f /app/alembic.ini ]; then
    alembic -c /app/alembic.ini upgrade head
fi

exec "$@"
