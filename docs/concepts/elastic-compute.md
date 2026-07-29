# Elastic compute

By default, [agents](agents.md) are **static**: an operator starts an agent host and it stays
running until it is stopped. **Elastic compute** lets the control plane instead *provision agents on
demand* and *terminate them when idle* — the "auto-terminating cluster" model. Work arriving for a
workspace with no available agent brings compute up; a stretch of inactivity takes it back down, so
nothing runs (and nothing is billed) while no queries are in flight.

!!! note "Opt-in and additive"
    Elastic compute is **off by default** (`ELASTIC_COMPUTE_ENABLED=false`) and does not change how
    static agents behave. Static and elastic agents coexist; a static agent has no lifecycle fields,
    an elastic one carries a provisioning lifecycle alongside its normal presence.

!!! note "Two backends"
    **Azure Container Instances**, provisioning into your own Azure subscription, and **Docker**,
    provisioning containers on the single host already running your stack. Bring-your-own-cloud
    (customer resource groups) and additional clouds are planned but **not yet shipped**.

## How it works

An elastic agent is the *same agent container* as a static one — it just gets started for you. The
control plane already knows how to enroll an agent (mint a one-time bootstrap token; the agent dials
home and registers). Elastic compute adds the missing step: **creating the container that runs the
agent**, and **tearing it down** when it goes idle. The agent is still outbound-only — the control
plane creates a *container*, it never dials the agent's control channel.

### Scaling out

Run a query against the **elastic pool** (the worksheet targets the pool instead of picking a
specific agent — over the API, omit `agent_id`):

1. If a compatible agent is already connected, the query dispatches to it, exactly as today.
2. Otherwise the run is parked **`queued`** and the control plane provisions one agent for the
   workspace's storage shape. Concurrent requests **coalesce** (a Postgres advisory lock) so a burst
   of queries provisions *one* agent, not one each.
3. When the provisioned agent dials home (~tens of seconds later), it picks up the parked queries and
   runs them. The client just polls the run as usual — no request is held open for the cold start.

An agent is matched to demand by its **pool key** — the set of storage-backend kinds it supports — so
one provisioned agent serves every workspace with the same storage shape.

Note that the pool key describes *capability*, not tenancy: it is what an agent can attach, not who
may use it. Access is the separate, explicit mechanism described in
[Per-agent access](permissions.md#per-agent-access). Auto-provisioned pool agents start **open**, so
scale-out serves whoever triggered it; restrict an agent only when you want it reserved.

### Creating compute manually

Compute can also be started deliberately from **Admin → Agents → New compute**, the way you'd start a
Databricks cluster. You pick a **named size** (vCPU + memory) and see its **hourly cost** before
creating it; the agent is provisioned at that size, appears in the list with its cost, and the same
idle reaper auto-terminates it when it goes quiet. The available sizes and their prices come from the
control plane (`GET /admin/agents/compute-options`), so cost is shown from one source of truth.

### Scaling in

A background reaper (leader-elected, like the scheduler and session reapers) terminates an elastic
agent when it has had **no work for the idle timeout** *and* has **no in-flight queries and no open
[SQL sessions](sql-sessions.md)** — an idle clock alone never tears down an agent that is mid-work. A
**max-lifetime** backstop bounds long-lived agents once their work drains.

## Lifecycle

An elastic agent's `lifecycle` is distinct from its socket *presence* (a running agent can briefly
disconnect without being torn down):

| State | Meaning |
|---|---|
| `provisioning` | The container is being created; the agent has not dialed home yet. |
| `running` | The agent has registered and is serving work. |
| `terminating` → `terminated` | The reaper is tearing the agent down (idle or max-lifetime). |
| `failed` | Provisioning never completed within the deadline, or the instance vanished. |

Because that column is mutated in place — a restart reuses the same agent row — it only ever
describes the agent *now*. Every transition is therefore also appended to a separate lifecycle
trail, together with the reason it happened (`idle`, `max_lifetime`, `provisioning_timeout`,
`restart`, `orphan`, `dead_row`). That trail is what the **Agent activity** chart on the agent's
[monitoring page](../operations/monitoring.md#per-agent-monitoring) is drawn from, and it is the
only record that survives a restart — without it, an agent that has been torn down and brought
back has no history at all.

The same trail is what lets the activity chart distinguish *not running* from *no data*: an agent
older than the trail has no recorded history, which is a weaker claim than knowing it was off.

The `reason` values are the same strings the reaper counts by in
`duckhaven_agents_reaped_total`, so a Prometheus alert and the UI cannot tell different stories
about why an agent went away.

## Reliability

Postgres is the single state-of-record: the agent row is written **before** the cloud instance is
created, so a crash mid-provision always leaves a reconcilable record. The reaper reconciles the
cloud against Postgres each cycle — a cloud instance with no live row is terminated (leak cleanup),
and a row whose instance has vanished is failed. The cloud is never a second source of truth.

## Related

- [Agents](agents.md) — the unit of compute elastic provisioning starts.
- [Elastic compute on a single Docker host](../deployment/homelab-elastic-setup.md) — enabling it on
  the box already running your stack.
- [Elastic compute on Azure](../deployment/azure-elastic-setup.md) — enabling it against Container
  Instances.
- [Query execution](query-execution.md) — how an agent admits and runs a query.
