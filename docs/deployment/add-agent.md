# Add an agent

Agents run DuckDB and execute queries dispatched by the control plane. One
agent per host/VM; agents always dial the control plane (so the control plane
needs no inbound connectivity to them).

## Prerequisites

- Linux host with Docker Engine 24+
- 8 GB RAM (DuckDB's working set)
- Network path to the control plane (Tailscale or direct)

## Generate a snippet from the admin UI

1. Sign in as an admin on the control plane.
2. **Compute → Generate bootstrap**.
3. Copy the rendered `docker-compose.yml` snippet. Example shape:

```yaml
services:
  duckhaven-agent:
    image: ghcr.io/tamasmrtn/duckhaven-agent:latest
    restart: unless-stopped
    environment:
      CONTROL_PLANE_URL: wss://duckhaven.example.com/agents/connect
      BOOTSTRAP_TOKEN: dh_boot_…
    volumes:
      - agent_results:/var/duckhaven-agent/results

volumes:
  agent_results:
```

The token is one-shot and expires in 24 hours. The control-plane URL is
derived from the request headers, so a TLS-fronted deploy gets `wss://`
automatically.

## Run on the new host

Save the snippet as `docker-compose.yml` on the new host and:

```bash
docker compose up -d
```

The agent dials home, exchanges the bootstrap token for a long-lived session
credential, and registers itself. Within ~10 s it should appear as `healthy`
in **Compute** on the control plane.

## Revoke

If a host is decommissioned: **Compute → click the agent → Revoke
credential**. The agent immediately drops to `unavailable`; the container can
then be stopped.

## Multiple agents on one host

You can run more than one agent container on the same host — give each a
distinct compose project (`docker compose -p agent-b ...`) and a unique
results volume name.

## Troubleshooting

See [the agent reference](../reference/agent-reference.md#troubleshooting) for capability detection,
extension errors, NAT / MTU pitfalls, and the result-server reachability
check.
