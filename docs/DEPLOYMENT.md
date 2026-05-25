# Deployment Guide

## Prerequisites

- Docker Engine 24+ and Docker Compose v2+
- A Linux host for the control plane (8 GB RAM minimum)
- One or more Linux hosts/VMs for agents (8 GB RAM per agent minimum)
- Network connectivity between agents and the control plane
- (Recommended) Tailscale for private network mesh

## Control Plane Deployment

The control plane is a single `docker compose` stack. It never runs DuckDB itself.

### 1. Configure environment

```bash
cp deploy/.env.example deploy/.env
```

Edit `deploy/.env`:

```bash
POSTGRES_PASSWORD=<strong-random-password>
SECRET_KEY=<strong-random-secret>
```

`SECRET_KEY` is used for session cookie signing. Generate one with:

```bash
openssl rand -hex 32
```

### 2. Start the stack

```bash
make compose-up
```

This starts Caddy, Postgres, Unity Catalog OSS, and the FastAPI app.

### 3. Run migrations

```bash
make migrate
```

### 4. Seed the admin user

```bash
make seed email=admin@example.com password=<strong-password>
```

This creates the first admin account. Run it once against a fresh database.

### 5. Access the app

Open `https://<control-plane-host>` (or `http://localhost:5173` if running the Vite dev server alongside).

## Agent Deployment

Agents run DuckDB and execute queries. They are **not** part of the control-plane compose stack.

### 1. Build the agent image

```bash
docker build -f agent/Dockerfile -t duckhaven-agent:latest .
```

### 2. Generate a bootstrap token

In the DuckHaven admin UI (Admin → Agents → Generate Bootstrap), create a one-time token. It expires in 24 hours.

Or via script:

```bash
# Requires a valid session cookie
scripts/gen-token.sh
```

### 3. Configure the agent

Create an `.env` file for the agent:

```bash
CONTROL_PLANE_URL=wss://<control-plane-host>/agents/connect
BOOTSTRAP_TOKEN=<token-from-step-2>
RESULTS_DIR=/var/duckhaven-agent/results
RESULTS_HTTP_PORT=8001
MEMORY_LIMIT_BYTES=6442450944
```

### 4. Run the agent

```bash
docker run -d \
  --name duckhaven-agent \
  -v /var/duckhaven-agent:/var/duckhaven-agent \
  -v /mnt/nas01:/mnt/nas01:ro \
  --env-file .env \
  duckhaven-agent:latest
```

Mount any local filesystem or NAS paths your workspaces need. The agent needs read/write on `RESULTS_DIR` and read access to backend roots.

### 5. Verify connection

In the admin UI, the agent should appear with status `healthy` and its capabilities listed (DuckDB version, extensions, memory limit).

## Storage Backends

Every workspace is bound to exactly one storage backend at creation time.

| Kind | URI prefix | Required agent extension | Notes |
|---|---|---|---|
| `local_fs` | `file:///var/duckhaven-agent/data/...` | none | Path must be mounted on every agent serving the workspace |
| `nas` | `file:///mnt/<name>/...` | none | NFS/SMB mounted on every agent |
| `s3` | `s3://bucket/prefix/...` | `httpfs` | Network — no mount needed |
| `adls_gen2` | `abfss://container@account/...` | `azure` | Network — no mount needed |

Register backends in the admin UI (Admin → Storage Backends) before creating workspaces.

## Networking

DuckHaven is designed for private networks. The recommended setup is:

- Control plane and all agents on the same Tailscale tailnet.
- Caddy serves HTTPS with `tls internal` (no public certificate needed).
- Agents dial the control plane outbound on WebSocket — no inbound ports required on agent hosts.

If not using Tailscale, ensure:
- Agents can reach `CONTROL_PLANE_URL` over WebSocket.
- The control plane can reach each agent's `RESULTS_HTTP_PORT` for result reads (or proxy through Caddy).
- All traffic is over HTTPS/WSS in production.

## Backup and DR

### Control plane

`scripts/pg-backup.sh` dumps Postgres to `/var/duckhaven/backups/`:

```bash
./scripts/pg-backup.sh
```

Schedule this via cron or systemd timer. Point the backup directory at a second disk or NAS for true DR.

### Data

Data lives on your storage backends, not in DuckHaven. Back up backend roots according to their kind:
- **Local FS / NAS** — use `restic`, `rsnapshot`, or your existing backup tool.
- **S3 / ADLS** — use object-store lifecycle policies and cross-region replication.

## Updating

```bash
# Pull latest code
git pull origin main

# Rebuild and restart
make compose-build
make compose-up
make migrate

# Restart agents with new image
docker pull duckhaven-agent:latest
docker restart duckhaven-agent
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent shows `unavailable` | Agent not connected | Check agent logs (`docker logs duckhaven-agent`), verify `CONTROL_PLANE_URL` and `BOOTSTRAP_TOKEN` |
| Query fails with `missing extension` | Agent lacks backend extension | Rebuild agent image or pick a different agent |
| UC connection errors | Unity Catalog not ready | Wait for UC healthcheck (`docker compose ps`), check `UC_BASE_URL` |
| Login fails | Wrong password or session expired | Reset admin password via `scripts/seed-admin.py` |
| Results not loading | Agent result server unreachable | Verify agent is running, check `RESULTS_HTTP_PORT` firewall rules |
