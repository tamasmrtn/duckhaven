# Installation

This page walks through standing up DuckHaven in detail. If you just want the fastest path to a first result, use
the [Quickstart](quickstart.md). For production concerns (TLS, external storage, backups), see
[Deployment](../deployment/install.md).

## Prerequisites

- Linux host with Docker Engine 24+ and Docker Compose v2
- 8 GB RAM minimum
- (Recommended) Tailscale or another private network for ingress — DuckHaven has no public ingress by design

## What the stack contains

DuckHaven runs as one Docker Compose stack:

| Service | Role |
|---|---|
| `postgres` | Application state and the Polaris metastore |
| `minio` | Bundled S3-compatible object storage (backs `object_store` workspaces) |
| `polaris` | Apache Polaris — Iceberg REST catalog and credential vendor |
| `api` | The control plane: serves the REST API and the web UI on port 8000 |
| `agent` | A bundled DuckDB compute agent that auto-registers |

## Install

```bash
curl -O https://raw.githubusercontent.com/tamasmrtn/duckhaven/main/deploy/docker-compose.yml
docker compose up -d
```

No `git clone` and no `.env` editing are required. On first boot the stack auto-generates `POSTGRES_PASSWORD`,
`SECRET_KEY`, and a one-shot first-admin setup token, and applies database migrations automatically.

## Create the first admin

Read the one-shot setup token on the host:

```bash
docker compose exec api cat /var/duckhaven/setup_token
```

Open `http://<host>:8000`, paste the token into the setup screen, and choose admin credentials. The token is consumed
after the admin is created.

## Reset

To wipe the stack and all its data (Postgres, secrets, and the setup token):

```bash
docker compose down -v
```

## Next steps

- [Create your first workspace](first-workspace.md).
- [Run your first query](first-query.md).
- Going to production? See [Deployment](../deployment/install.md) for TLS, storage, and backups.
