#!/bin/sh
# Generate the persistent app secret on first boot, then read it and construct
# DATABASE_URL before launching the API. This folds in the former init-secrets
# one-shot: there is no separate bootstrap container, so the api service
# prepares its own secrets idempotently on every start.
set -eu

SECRETS_DIR="${SECRETS_DIR:-/var/duckhaven/secrets}"
DATA_DIR="${DATA_DIR:-/var/duckhaven}"
mkdir -p "$SECRETS_DIR"

# ── Secret generation (idempotent; first boot only) ──────────────────────────
# Write a file only if it does not already exist. An env-provided SECRET_KEY is
# captured on first boot and persisted; on later boots the file wins so the
# stack survives a missing .env.
write_if_absent() {
    name=$1
    env_val=$2
    path="$SECRETS_DIR/$name"
    if [ -s "$path" ]; then
        return 0
    fi
    if [ -n "$env_val" ]; then
        printf '%s' "$env_val" > "$path"
    else
        # 32 random bytes, base64-encoded, newline stripped.
        head -c 32 /dev/urandom | base64 | tr -d '\n' > "$path"
    fi
    chmod 644 "$path"
}

# Detect first boot via the cornerstone secret. The setup_token gates the
# browser-driven first-admin creation (POST /api/setup/admin); it is generated
# ONLY on first boot so a stranger reading the volume after the operator has
# already created the admin cannot mint a fresh token.
if [ ! -s "$SECRETS_DIR/secret_key" ]; then
    FIRST_BOOT=1
else
    FIRST_BOOT=0
fi

write_if_absent secret_key "${SECRET_KEY:-}"

if [ "$FIRST_BOOT" = 1 ]; then
    # Written to DATA_DIR (api_data volume), not SECRETS_DIR, so the api
    # container can delete it after first-admin creation.
    TOKEN_PATH="$DATA_DIR/setup_token"
    if [ ! -s "$TOKEN_PATH" ]; then
        head -c 32 /dev/urandom | base64 | tr -d '\n' > "$TOKEN_PATH"
        chmod 644 "$TOKEN_PATH"
    fi
fi

# ── Runtime env ──────────────────────────────────────────────────────────────
SECRET_KEY="$(cat "$SECRETS_DIR/secret_key")"
export SECRET_KEY

# A DATABASE_URL supplied by the environment wins. Assembling one here can only
# ever express user-and-password auth, so overriding it unconditionally would make
# the image dictate the credential model to every deployment that uses it -- a
# passwordless connection (Azure managed identity, where the password is a
# short-lived token the driver fetches per connection) has no password to put in a
# URL at all.
#
# With it unset, the URL is built from the POSTGRES_* variables below. That is the
# compose path: the password is shared with the postgres/polaris services via
# compose interpolation, Postgres is never published, so this internal-only
# password is overridable but not vended, and the default keeps a fresh stack
# working with zero .env edits.
if [ -z "${DATABASE_URL:-}" ]; then
    POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-duckhaven}"
    DB_USER="${POSTGRES_USER:-duckhaven}"
    DB_HOST="${POSTGRES_HOST:-postgres}"
    DB_PORT="${POSTGRES_PORT:-5432}"
    DB_NAME="${POSTGRES_DB:-duckhaven}"
    DATABASE_URL="postgresql+asyncpg://${DB_USER}:${POSTGRES_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi
export DATABASE_URL

# Apply pending migrations before the app starts. Only runs inside the built
# image where /app/alembic.ini exists; in unit tests of this script the file
# is absent and the step is skipped.
if [ -f /app/alembic.ini ]; then
    alembic -c /app/alembic.ini upgrade head
fi

exec "$@"
