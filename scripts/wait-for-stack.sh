#!/usr/bin/env bash
# Wait for the compose stack's API and agent containers to report healthy.
# Used by the E2E CI job (and handy locally after `make compose-up`).
set -euo pipefail

COMPOSE="docker compose -f deploy/docker-compose.yml"

wait_healthy() {
  local svc=$1
  local tries=${2:-60}
  for _ in $(seq 1 "$tries"); do
    cid=$($COMPOSE ps -q "$svc" || true)
    if [ -n "$cid" ]; then
      status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")
      if [ "$status" = "healthy" ]; then
        echo "$svc is healthy"
        return 0
      fi
    fi
    sleep 5
  done
  echo "ERROR: $svc did not become healthy in time" >&2
  $COMPOSE logs "$svc" | tail -80 >&2
  return 1
}

wait_healthy api
wait_healthy agent
echo "Stack is ready."
