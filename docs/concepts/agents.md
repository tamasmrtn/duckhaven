# Agents

An **agent** is DuckHaven's unit of compute: a Python process that embeds a DuckDB engine, runs on its own host, and
*dials home* to the control plane over a WebSocket. Agents are the only component that executes user SQL.

## Transparent compute

Users pick **which agent runs each query** from the worksheet's engine picker. There is no distributed query planner
and no cost-based routing — compute is explicit and transparent. Add an agent host when you need more capacity; see
[Scaling compute](../operations/scaling.md).

## Cattle that dial home

An agent needs only a control-plane URL and a one-time [bootstrap token](../deployment/add-agent.md). It registers
itself, advertises its capabilities, and holds one outbound WebSocket open. The control plane keeps no static
inventory of agent addresses and never dials an agent's control channel.

Agents can be run by an operator (static) or provisioned automatically and torn down when idle — see
[Elastic compute](elastic-compute.md).

## Capabilities and backend compatibility

On connect (and on every heartbeat) an agent advertises its capabilities — DuckDB version, loaded extensions, memory
ceiling, cores, and host. DuckHaven matches agents to a workspace's [storage backend](storage-backends.md) by required
extension (for example, `azure` for ADLS), and incompatible agents are shown disabled before a query is sent.

## Live utilization and history

Each heartbeat also carries live running/queued query counts and the active concurrency profile. Every agent has its
own **Monitoring** page (Admin → Agents → *an agent*) showing those counters live, plus 1–24 hours of query
throughput, saturation, failures, utilization, and an up/down timeline. See
[Monitoring](../operations/monitoring.md#per-agent-monitoring).

## Related

- [Add an agent](../deployment/add-agent.md) — register a new agent.
- [Agent reference](../reference/agent-reference.md) — configuration, extensions, and troubleshooting.
- [Query execution](query-execution.md) — how an agent admits and runs a query.
