---
type: troubleshooting
---

# Troubleshoot compute

Find the message you see in an agent's log, on the Compute page, or in a query's error, then follow what to do.

Read a static agent's log on its host with `docker compose logs duckhaven-agent`, or `docker compose logs agent` for the
bundled one. A query's errors are listed in [Troubleshoot queries](../querying/troubleshooting.md).

## "Disconnected (received 1008 (policy violation) Invalid token …), reconnecting in 5s"

**What it means.** The control plane refused the agent's token. A bootstrap token from **Generate bootstrap** works
once and expires after 24 hours; a revoked agent's credential stops working at once.

**What to do.** Generate a new snippet under **Compute → Generate bootstrap**, put its `BOOTSTRAP_TOKEN` in the
agent's environment, and restart it. An agent that registered once keeps its own credential and does not need the
token again.

## The agent shows "unavailable"

**What it means.** The agent is not connected: it stopped, cannot reach the control plane, or its credential was
revoked.

**What to do.**

1. Read the agent's log for the reason.
2. From the agent's host, check the control plane answers: `curl -I https://duckhaven.example.com`.
3. Check nothing between them blocks the WebSocket at `/agents/connect`, such as a proxy that does not pass
   WebSocket upgrades; see [Reverse proxy & TLS](../deployment/reverse-proxy-tls.md).

## The agent disconnects often

**What it means.** The long-lived WebSocket between the agent and the control plane keeps dropping. The agent
reconnects after 5 seconds each time, and queries running on it fail.

**What to do.** Check the network between them. Behind NAT or a load balancer, make sure idle long-lived connections
are not closed. Tailscale usually keeps them open; a path over the internet may need its MTU lowered.

## "Agent not connected"

**What it means.** A query named an agent that is offline and that DuckHaven cannot start.

**What to do.** Choose another agent, or bring this one back as above. A stopped [elastic](elastic-compute.md) agent
is started instead of refused.

## An agent shows "Can't serve this workspace"

**What it means.** The agent lacks an extension the workspace's catalogs need, or runs a
[runtime](runtimes.md) the control plane does not send work to. The picker shows which. Through the API the refusal is
**422** `agent_incompatible` or `runtime_unsupported`.

**What to do.** Use an agent whose runtime has the extension; [Agents](agents.md#extensions) lists them. Update an old
agent image to the control plane's version.

## "Compute is starting; retry shortly."

**What it means.** [Elastic compute](elastic-compute.md) is starting an agent and it did not come up within the wait
(**503** `compute_starting`).

**What to do.** Retry after the `Retry-After` header; the retry lands on the agent that is starting. If it never
arrives, see the next entry.

## "Could not start compute for this session." or "Could not start the configured agent."

**What it means.** The elastic backend failed to start an agent (**503** `compute_unavailable`).

**What to do.** Read the API's log for the backend's reason, then see
[Elastic compute on a single Docker host](elastic-docker.md#if-it-goes-wrong) or
[Elastic compute on Azure](elastic-azure.md#if-it-goes-wrong).

## An elastic agent stays "provisioning", then "failed"

**What it means.** The container started but the agent never connected within `ELASTIC_PROVISIONING_DEADLINE_S`, or
the container never started. Runs waiting for it fail with the deadline.

**What to do.** Check the agent container's log, where it exists, for a connection error, and the API's log for a
provisioning error.

## Results of a finished query fail to load

**What it means.** Results stay on the agent that ran the query, and the control plane reads them from its result
server. The agent is gone, or its result port is not reachable from the control plane.

**What to do.** Check the agent is running and that the control plane reaches its `RESULTS_HTTP_PORT` (8001). If the
agent is reached at another address than it connects from, set `RESULT_ADVERTISE_HOST`; see
[Agent configuration](../reference/agent-configuration.md#results).

## "This action on agent '⟨name⟩' requires the '⟨tier⟩' tier."

**What it means.** You may see the agent, but your tier on it is too low for this action.

**What to do.** Ask someone with `admin` on the agent, or an admin with `agents:manage`, for a higher tier; see
[Agent access](../access/agent-access.md).

## Related

- [Add an agent](add-agent.md)
- [Agent monitoring](agent-monitoring.md) — whether an agent is saturated.
- [Troubleshoot queries](../querying/troubleshooting.md)
- [Troubleshooting the deployment](../operations/troubleshooting.md)
