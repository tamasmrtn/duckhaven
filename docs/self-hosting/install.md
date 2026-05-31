# Install DuckHaven

DuckHaven runs as one `docker compose` stack — Postgres, Unity Catalog, and the
DuckHaven API (which serves both the REST API and the web UI on port 8000). No
`git clone` is required.

## Prerequisites

- Linux host with Docker Engine 24+ and Docker Compose v2
- 8 GB RAM minimum
- (Recommended) Tailscale or another private network for ingress

## Install

```bash
curl -O https://raw.githubusercontent.com/tamasmrtn/duckhaven/main/deploy/docker-compose.yml
docker compose up -d
```

On first boot the stack auto-generates `POSTGRES_PASSWORD`, `SECRET_KEY`, and
a one-shot first-admin setup token, and applies Alembic migrations
automatically. No `.env` editing required.

## Create the first admin

Read the setup token on the host:

```bash
docker compose exec api cat /var/duckhaven/secrets/setup_token
```

Open `http://<host>:8000` in a browser. The SPA detects an empty database and
routes you to the setup screen — paste the token, pick admin credentials,
submit. The token is consumed (deleted) after the admin is created and is not
regenerated on subsequent boots.

To start over, wipe the stack:

```bash
docker compose down -v
```

(This wipes Postgres, secrets, and the setup token.)

## Add agents

Agents are deployed to separate hosts. See [add-agent.md](./add-agent.md).

## Next steps

- [Update](./update.md) — pull a new release.
- [Reverse proxy + TLS](./reverse-proxy.md) — front the stack with Caddy.
- [Backup and restore](./backup-restore.md) — protecting Postgres.
