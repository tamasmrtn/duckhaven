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
| `minio` | Bundled S3-compatible object storage (backs `object_store` catalogs) |
| `polaris-bootstrap` | One-shot: provisions the Polaris realm, then exits |
| `polaris` | Apache Polaris — Iceberg REST catalog and credential vendor |
| `api` | The control plane: serves the REST API and the web UI on port 8000 |
| `agent` | A bundled DuckDB compute agent that auto-registers |
| `otel-collector` | Receives traces from the API and agent |
| `tempo` | Stores those traces (72h retention) |
| `grafana` | Queries Tempo — see [Distributed tracing](../operations/tracing.md) |

## Install

```bash
curl -fsSL https://github.com/tamasmrtn/duckhaven/archive/refs/heads/main.tar.gz \
  | tar xz --strip-components=2 duckhaven-main/deploy
docker compose up -d
```

No `git clone` and no `.env` editing are required. The tarball unpacks the `deploy/` directory rather than just the
compose file, because compose mounts several sibling files — the Postgres init script, the Polaris bootstrap wrapper,
and the collector configs — and the stack will not start without them.

On first boot the stack generates `SECRET_KEY` and a one-shot first-admin setup token, and applies database
migrations automatically.

!!! warning "Set `POSTGRES_PASSWORD` before exposing anything"
    Postgres falls back to the default password `duckhaven` when `POSTGRES_PASSWORD` is unset — a published default.
    Set it in the environment before the first boot on anything but a private box you trust.

## Create the first admin

Read the one-shot setup token on the host:

```bash
docker compose cp api:/var/duckhaven/setup_token ./setup_token && cat ./setup_token
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
