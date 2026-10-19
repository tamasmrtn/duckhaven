---
type: how-to
---

# Add an agent

Run a static agent on another host: generate a bootstrap snippet, start it with Docker Compose, and watch it register.

An agent always connects out to the control plane, so the control plane needs no inbound route to the agent's host.
For agents that DuckHaven starts and stops itself, see [Elastic compute](elastic-compute.md).

## Before you begin

- **You need:** the `agents:manage` permission, which the `admin` role holds.
- **The host:** Linux with Docker Engine 24 or later, about 8 GB of memory for DuckDB, and a network path to the
  control plane, such as Tailscale or a direct route.

## 1. Generate a snippet

1. Open **Compute** and click **Generate bootstrap**.
2. If the control plane offers more than one [runtime](runtimes.md), choose one; the default is preselected.
3. Copy the `docker-compose.yml` snippet. It has this shape:

```yaml
services:
  duckhaven-agent:
    image: ghcr.io/tamasmrtn/duckhaven-agent:0.8.0-duckdb1.5
    restart: unless-stopped
    environment:
      CONTROL_PLANE_URL: wss://duckhaven.example.com/agents/connect
      BOOTSTRAP_TOKEN: dh_boot_…
    volumes:
      - agent_results:/var/duckhaven-agent/results

volumes:
  agent_results:
```

- The token works once and expires after **24 hours**.
- The control-plane address comes from your browser's request, so a deployment behind TLS gets `wss://`.
- The image is the runtime's, at the control plane's own version: `-duckdb1.5` is the DuckDB 1.5 runtime. An agent on
  a runtime the control plane does not know is refused work, so upgrade the control plane before introducing a new
  runtime.

Other settings go in the same `environment:` block; see [Agent configuration](../reference/agent-configuration.md).

## 2. Start it on the host

Save the snippet as `docker-compose.yml` on the new host and run:

```bash
docker compose up -d
```

The agent connects, exchanges the bootstrap token for a long-lived credential it keeps in its results volume, and
registers. Restarts reuse that credential; the bootstrap token is not needed again.

## Check that it worked

Within about ten seconds the agent appears in **Compute** as `healthy`, and its **Overview** tab lists its runtime,
DuckDB version and extensions. It then appears in the worksheet's agent picker for anyone who may use it.

## Run more than one agent on a host

Give each agent its own compose project and results volume, and its own bootstrap token, since each token works once:

```bash
docker compose -p agent-b up -d
```

Each agent sizes itself from its container's memory limit, so give each container the memory you want its queries to
use, plus room for the operating system.

## Run an agent without Docker

From a checkout of the repository:

```bash
cd agent
uv sync
export CONTROL_PLANE_URL=wss://duckhaven.example.com/agents/connect
export BOOTSTRAP_TOKEN=dh_boot_…
uv run python -m agent.main
```

It runs the DuckDB version in `uv.lock`, the default runtime's line. The extensions in
[Agents](agents.md#extensions) must be installed in `~/.duckdb`, for example with `make build-agent` or an `INSTALL`
for each.

## Remove an agent

Open **Compute**, click the agent, and click **Revoke credential**. The agent is disconnected at once and shows as
`unavailable`; stop its container and delete its results volume once you are done with them.

## If it goes wrong

The agent's own log says why it cannot connect:

```bash
docker compose logs duckhaven-agent
```

- **The token expired or was used.** Generate a new snippet. An agent that registered once keeps working without it.
- **The agent cannot reach the control plane.** From the host, `curl -I https://duckhaven.example.com` must answer,
  and nothing between them may block the WebSocket at `/agents/connect`.
- **It registers but cannot run work.** Its runtime or extensions do not fit the workspace's catalogs.

These and other cases are in [Troubleshoot compute](troubleshooting.md).

## Related

- [Agents](agents.md) — what an agent reports and who may use it.
- [Scaling compute](scaling.md) — when to add agents.
- [Agent configuration](../reference/agent-configuration.md) — every setting.
