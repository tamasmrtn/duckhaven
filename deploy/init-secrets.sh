#!/bin/sh
# Generate persistent secrets on first boot. Idempotent: writes a file only if
# it does not already exist. An env-provided SECRET_KEY / POSTGRES_PASSWORD is
# captured on first boot and persisted; on later boots the file wins so the
# stack survives a missing .env.
set -eu

SECRETS_DIR="${SECRETS_DIR:-/var/duckhaven/secrets}"
mkdir -p "$SECRETS_DIR"

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
write_if_absent postgres_password "${POSTGRES_PASSWORD:-}"

if [ "$FIRST_BOOT" = 1 ]; then
    write_if_absent setup_token ""
fi
