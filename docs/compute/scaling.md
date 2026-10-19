---
type: concept
---

# Scaling compute

Add agents for more capacity, give an agent more memory for heavier queries, or change how one agent shares its memory
between queries.

The control plane stays small; capacity is the agents. Each agent is an independent DuckDB process, so scaling is
adding agents or making them larger. With [elastic compute](elastic-compute.md), the pool adds and removes agents for
you, up to `ELASTIC_MAX_AGENTS_PER_POOL`.

## When to add or grow an agent

| What you see | What it means | What to do |
|---|---|---|
| A persistent **waiting** layer in [Agent monitoring](agent-monitoring.md), or `queue full` errors | The agent is saturated | Add an agent, or switch to a profile with more slots if each query still gets enough memory |
| Queries spill to disk, or fail with out-of-memory errors | Queries need more memory than the agent has | Give the agent's container more memory |
| An agent shows **Can't serve this workspace** | It lacks an extension a catalog needs | Run an agent on a runtime that has it |

To add an agent, see [Add an agent](add-agent.md); agents connect out, so the control plane needs no route to them. An
agent sizes itself from its container's memory limit, so giving the container more memory is how you make an agent
larger.

## How one agent shares its memory

Each agent admits queries up to its memory budget, the container's memory less a headroom
(`MEMORY_HEADROOM_FRACTION`, 10% by default), and queues the rest in arrival order. The **concurrency profile** decides
how the budget is split:

| Profile | Share of the budget | Use it when |
|---|---|---|
| `auto` (default) | Each query reserves what its plan needs, learned from earlier runs; idle memory is lent on top | Almost always |
| `single` | One query at a time gets all of it | Every query is huge |
| `equal_2` | 50% / 50% | Two heavy queries at once |
| `decaying_2` | 67% / 33% | One heavy query, one light |
| `decaying_3` | 50% / 33% / 17% | One heavy, two lighter |

The profile divides memory only: every query gets all the agent's cores under every profile. Set the default with
`MAX_CONCURRENCY_PROFILE`, or change it while the agent runs, for every query on it, with
`SET duckhaven_concurrency = 'decaying_3'` in a worksheet that has the agent selected. A change made that way lasts
until the agent restarts. See [Query execution](../querying/query-execution.md#admission-control-and-concurrency) for
how `auto` sizes queries.

The queue holds `MAX_QUEUE_DEPTH` queries (100); one more fails with `queue full`. `QUEUED_TIMEOUT_S`, off by default,
fails a query that waits too long with `queued timeout`.

## Control-plane high availability

The control plane runs on one machine by default, which suits most installs. For deployments that cannot tolerate
that machine failing, run Postgres highly available and several API replicas behind a load balancer; see
[High availability](../deployment/high-availability.md).

## Related

- [Agent monitoring](agent-monitoring.md) — whether an agent is saturated.
- [Agent configuration](../reference/agent-configuration.md) — the admission settings.
- [Elastic compute](elastic-compute.md) — agents that start and stop with demand.
