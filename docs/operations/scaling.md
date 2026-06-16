# Scaling compute

DuckHaven scales compute horizontally: the control plane stays small (one box) while you add
[agents](../concepts/agents.md) at the edge.

## Add agents for more capacity

Each agent is an independent DuckDB process on its own host. To add capacity, register another agent — see
[Add an agent](../deployment/add-agent.md). Agents dial home on their own; the control plane needs no inbound
connectivity to them. Users then pick which agent runs each query.

When to add one:

- **Persistent queued count** in [Monitoring](monitoring.md) means an agent is saturated.
- **Heavier workloads** benefit from an agent with more RAM (a higher memory ceiling).
- **Backend reach** — an agent must carry the extension a workspace's [storage backend](../concepts/storage-backends.md)
  needs (for example `azure` for ADLS).

## Tune a single agent

Within one agent, concurrency is governed by admission control. The default `auto` profile sizes each query's memory
reservation from its `EXPLAIN` plan; static slot ladders (`single`, `equal_2`, `decaying_2`, `decaying_3`) are
available as alternatives. See [Runbook §6](runbook.md#6-query-queueing-concurrency) and
[Query execution](../concepts/query-execution.md).

## What does not scale horizontally

The control plane (Postgres + Polaris + API) is intentionally single-node; high availability is a roadmap item, not a
current property.
