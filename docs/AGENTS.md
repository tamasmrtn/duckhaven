# Agent Setup Guide

## What Is an Agent?

A DuckHaven agent is a Python process that embeds DuckDB and connects to the control plane via WebSocket. It executes SQL queries dispatched by users and serves result Parquet files over HTTP.

Agents are the only component that runs DuckDB. The control plane does not.

## Prerequisites

- Linux host/VM (8 GB RAM minimum)
- Docker (recommended) or Python 3.14+ with `uv`
- Network reachability to the control plane's `/agents/connect` WebSocket endpoint
- (Optional) Mounted storage paths for local FS or NAS backends

## Quick Start

### 1. Generate a bootstrap token

In the DuckHaven admin UI:

1. Navigate to **Admin → Agents**.
2. Click **Generate Bootstrap Token**.
3. Copy the token (it looks like `dh_boot_...`).

This token is single-use and expires in 24 hours.

### 2. Build or pull the agent image

```bash
# From the repo root
docker build -f agent/Dockerfile -t duckhaven-agent:latest .
```

Or pull a published image (once available):

```bash
docker pull ghcr.io/tamasmrtn/duckhaven-agent:latest
```

### 3. Create agent configuration

Create a file named `agent.env`:

```bash
# Required
CONTROL_PLANE_URL=ws://duckhaven.example.com:8000/agents/connect
BOOTSTRAP_TOKEN=dh_boot_xxxxxxxxxxxxxxxx

# Optional (defaults shown)
RESULTS_DIR=/var/duckhaven-agent/results
RESULTS_HTTP_PORT=8001
MEMORY_LIMIT_BYTES=6442450944
```

### 4. Run the agent

```bash
docker run -d \
  --name duckhaven-agent \
  -v /var/duckhaven-agent:/var/duckhaven-agent \
  -v /mnt/data:/mnt/data:ro \
  --env-file agent.env \
  --restart unless-stopped \
  duckhaven-agent:latest
```

Mount any directories your workspaces need:
- `/var/duckhaven-agent` — for query results (read/write)
- `/mnt/data` or similar — for local FS / NAS backend roots (read)

### 5. Verify in the UI

Go to **Admin → Agents**. Your agent should appear with:
- Status: `healthy`
- DuckDB version
- Loaded extensions
- Memory limit
- Hostname

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `CONTROL_PLANE_URL` | Yes | — | WebSocket URL to the control plane's agent endpoint |
| `BOOTSTRAP_TOKEN` | Yes | — | One-time bootstrap token from admin UI |
| `RESULTS_DIR` | No | `/var/duckhaven-agent/results` | Directory for materialized query results |
| `RESULTS_HTTP_PORT` | No | `8001` | Port for the local HTTP result server |
| `MEMORY_LIMIT_BYTES` | No | `6442450944` (6 GB) | Per-query memory ceiling |

## Extensions and Backend Compatibility

The agent image pre-installs these DuckDB extensions at build time:

| Extension | Required for |
|---|---|
| `httpfs` | S3 storage backends |
| `azure` | ADLS Gen 2 storage backends |
| `unity_catalog` | Unity Catalog integration |
| `delta` | Delta Lake table reads and writes |

The agent advertises its loaded extensions to the control plane on connect. The frontend engine picker shows which backends each agent can serve. For example, an agent without the `azure` extension cannot execute queries against ADLS workspaces.

## Running Without Docker

If you prefer to run the agent directly:

```bash
# From the repo root
cd agent
uv sync

# Set environment variables
export CONTROL_PLANE_URL=ws://...:8000
export BOOTSTRAP_TOKEN=dh_boot_...

# Run
uv run python -m agent.main
```

Ensure DuckDB ≥1.5 is installed and the extensions above are available.

## Multiple Agents

You can run multiple agents on the same host or different hosts. Each agent:
- Needs its own `RESULTS_DIR` (or will overwrite another agent's results).
- Needs its own `BOOTSTRAP_TOKEN` (each token is single-use).
- Should have sufficient memory for its `MEMORY_LIMIT_BYTES` plus OS overhead.

Example: two agents on one host with Docker Compose:

```yaml
services:
  agent-a:
    image: duckhaven-agent:latest
    env_file: agent-a.env
    volumes:
      - /var/duckhaven-agent-a:/var/duckhaven-agent/results
      - /mnt/data:/mnt/data:ro

  agent-b:
    image: duckhaven-agent:latest
    env_file: agent-b.env
    volumes:
      - /var/duckhaven-agent-b:/var/duckhaven-agent/results
      - /mnt/data:/mnt/data:ro
```

## Troubleshooting

### Agent shows "unavailable" in the UI

- Check agent logs: `docker logs duckhaven-agent`
- Verify `CONTROL_PLANE_URL` is reachable from the agent host: `curl -I https://duckhaven.example.com`
- Verify the bootstrap token has not expired (24h TTL).
- Check that the control plane's `/agents/connect` WebSocket endpoint is not blocked by firewall.

### Queries fail with extension errors

- Rebuild the agent image to ensure extensions are pre-installed.
- Check the agent's advertised extensions in the admin UI drawer.
- Pick a different agent that has the required extension.

### Result rows fail to load

- Verify the agent is still running.
- Check that the control plane can reach the agent's `RESULTS_HTTP_PORT`.
- Verify the agent's session token is valid (revoke and re-register if needed).

### Agent disconnects frequently

- Check network stability between agent and control plane.
- If behind NAT, ensure WebSocket long-lived connections are not dropped.
- Tailscale generally handles this automatically; raw internet may need MTU tuning.

## Revoking an Agent

To remove an agent:

1. In the admin UI, go to **Admin → Agents**.
2. Click the agent row to open the drawer.
3. Click **Revoke Credential**.

The agent will be disconnected and marked `unavailable`. Its results directory can be cleaned up manually.
