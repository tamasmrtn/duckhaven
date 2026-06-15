# Agent reference

> **Installing an agent?** See
> [Add an agent](../deployment/add-agent.md) for the
> task-oriented walkthrough (generate snippet → paste → `docker compose up -d`).
> This page is the technical reference: configuration, extensions,
> multi-agent layouts, and troubleshooting.

A DuckHaven agent is a Python process that embeds DuckDB, connects to the
control plane via WebSocket, executes SQL queries dispatched by users, and
serves result Parquet files over HTTP. Agents are the only component that
runs DuckDB.

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `CONTROL_PLANE_URL` | Yes | — | WebSocket URL to the control plane's agent endpoint |
| `BOOTSTRAP_TOKEN` | Yes | — | One-time bootstrap token from admin UI |
| `RESULTS_DIR` | No | `/var/duckhaven-agent/results` | Directory for materialized query results |
| `RESULTS_HTTP_PORT` | No | `8001` | Port for the local HTTP result server |
| `MEMORY_LIMIT_BYTES` | No | `6442450944` (6 GB) | Per-query memory ceiling |
| `MAX_CONCURRENCY_PROFILE` | No | `auto` | Reservation sizing: `auto` (EXPLAIN-estimated per query) or a static slot ladder (`single`/`equal_2`/`decaying_2`/`decaying_3`). See [Runbook §6](runbook.md#6-query-queueing-concurrency). |
| `PROFILING_ENABLED` | No | `true` | Capture DuckDB's post-execution query profile and return it on `query_done`. Set `false` to disable. |

The `auto` profile has additional best-effort estimator knobs
(`ESTIMATE_SAFETY_MULTIPLIER`, `ESTIMATE_FLOOR_BYTES`,
`ESTIMATE_CEILING_FRACTION`, `EXPLAIN_TIMEOUT_S`, `ESTIMATE_FALLBACK_BUCKET`) and
the queueing knobs (`MEMORY_HEADROOM_FRACTION`, `MAX_QUEUE_DEPTH`,
`QUEUED_TIMEOUT_S`); see [Runbook §6](runbook.md#6-query-queueing-concurrency).

## Extensions and Backend Compatibility

The agent image pre-installs these DuckDB extensions at build time:

| Extension | Required for |
|---|---|
| `httpfs` | S3 storage backends |
| `azure` | ADLS Gen 2 storage backends |
| `iceberg` | Apache Iceberg reads/writes + Polaris REST catalog attach |

The agent advertises its loaded extensions to the control plane on connect. The
frontend engine picker shows which backends each agent can serve. For example,
an agent without the `azure` extension cannot execute queries against ADLS
workspaces.

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
