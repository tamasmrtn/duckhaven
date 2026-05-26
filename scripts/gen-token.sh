#!/usr/bin/env bash
# Generate a bootstrap token for a new agent via the duckhaven API.
# Usage: SESSION_COOKIE=<value> ./scripts/gen-token.sh
set -euo pipefail

: "${SESSION_COOKIE:?Set SESSION_COOKIE to your session cookie value}"
: "${API_URL:=http://localhost:8000}"

curl -sSf -X POST \
    -H "Cookie: session=${SESSION_COOKIE}" \
    "${API_URL}/admin/agents/bootstrap" | jq .
