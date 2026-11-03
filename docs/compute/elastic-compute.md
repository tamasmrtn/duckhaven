---
type: concept
---

# Elastic compute

Elastic compute starts agents when work arrives and none is running, and stops them after they sit idle, so nothing
runs, or is billed, between queries.

By default, [agents](agents.md) are **static**: an operator starts an agent host and it runs until it is stopped. With
elastic compute the control plane creates agent containers itself, on demand, and removes them when idle, like an
auto-terminating cluster. Static and elastic agents work side by side.

!!! info "Availability"
    Off until an operator sets `ELASTIC_COMPUTE_ENABLED=true`. Two backends:
    [Docker](elastic-docker.md), which starts containers on the host running your stack, and
    [Azure Container Instances](elastic-azure.md), which starts them in your Azure subscription. Creating compute by
    hand needs `agents:manage`.

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

A workspace that has a [default compute](#a-workspaces-default-compute) skips the pool: its work runs on that agent,
and DuckHaven never adds a pool agent for it.

An agent is matched to demand by its **pool key** — the set of storage-backend kinds it supports — so
one provisioned agent serves every workspace with the same storage shape.

Pool agents run the deployment's **default [runtime](runtimes.md)** (`DEFAULT_RUNTIME`): nobody named an agent, so
nobody chose a DuckDB version. The pool's cap is counted per runtime. After you change the default, new supply is
provisioned on the new runtime straight away, while agents on the old one keep serving until the idle reaper takes
them down.

An agent registers before it reports what it is, so the parked work waits until it has reported. Then each run is
dispatched with the same checks as any other: the agent must have the catalogs' extensions and a runtime that is
trusted with work. A pool agent that reports the wrong runtime is terminated rather than left holding the pool's slot.

Note that the pool key describes *capability*, not tenancy: it is what an agent can attach, not who
may use it. Access is the separate, explicit mechanism described in
[Per-agent access](../access/agent-access.md). Auto-provisioned pool agents start **open**, so
scale-out serves whoever triggered it; restrict an agent only when you want it reserved.

### Creating compute manually

Compute can also be started deliberately from the **Compute** section of the nav, via **New compute**.
You pick a **named size** (vCPU + memory) and see its **hourly cost** before
creating it; the agent is provisioned at that size, appears in the list with its cost, and the same
idle reaper auto-terminates it when it goes quiet. The available sizes and their prices come from the
control plane (`GET /api/admin/agents/compute-options`), so cost is shown from one source of truth.

The same dialog chooses **who can use it** — anyone signed in, or only the people you grant access
to. Deciding that at creation rather than on the Access tab afterwards matters; see
[Per-agent access](../access/agent-access.md).

It also chooses the **[runtime](runtimes.md)**, meaning the DuckDB version and extensions the agent runs. The default
runtime is preselected. A beta runtime needs an explicit opt-in, and deprecated or retired runtimes aren't offered.
The runtime stays with the agent: every restart brings it back on the same runtime.

### Starting a terminated agent by naming it

Naming a specific elastic agent the reaper has torn down **starts it** rather than
failing. The work is parked `queued` with no agent — exactly what the worksheet already
shows for a pool run during a cold start — and dispatches to that agent when it dials
home.

It has to work this way. The reaper terminated the agent *because* nothing was using it,
so refusing the next run would make an idle-terminated agent permanently unusable — and
for a [schedule](../querying/schedule-queries.md) bound to one, every run would fail on the
consequence of the previous one succeeding.

It comes back on the runtime it was created with, running the newest build of that runtime the control plane ships.
If that runtime has since been retired, the run is refused (`runtime_retired`) instead of parked, since the agent
can never start again.

This needs only the `use` tier: sending work is dispatch, not a lifecycle operation.
Restarting an agent deliberately, with no work to justify it, still needs `operate`; see
[Per-agent access](../access/agent-access.md).

**A [SQL session](../querying/sql-sessions.md)** — how dbt, dlt and other Python DB-API clients connect — is the one
case that cannot park and answer, because a session open is *synchronous*: it has to hand back something the
caller can immediately run statements on. It therefore blocks while compute starts, and the caller chooses how that wait
ends — see [Cold start](../querying/sql-sessions.md#cold-start).

### Work waiting for compute

A query that finds no agent ready is parked `queued` until one takes it. Three limits keep that wait bounded:

- **A cap on how many wait.** A workspace may have `COMPUTE_QUEUE_MAX_PER_WORKSPACE` queries waiting (100 by default),
  and the deployment `COMPUTE_QUEUE_MAX_TOTAL` (500). Past a cap a new query is refused with `compute_queue_full`:
  **429** for the workspace cap, **503** for the deployment one, each with a `Retry-After`. The refused attempt is
  recorded as a failed query. Scheduled runs neither count nor are refused. A query an agent takes at once never waits,
  so it is never refused.
- **A limit on how long.** A waiting query is failed after `COMPUTE_QUEUE_WAIT_TIMEOUT_S`, which is the provisioning
  deadline unless you set it.
- **An order.** When an agent registers, the waiting queries are dispatched oldest first.

### A workspace's default compute

A workspace owner can choose one agent as the workspace's [default compute](../access/workspaces.md#default-compute).
Work that names no agent runs there: an API call without `agent_id`, a [SQL session](../querying/sql-sessions.md), a
describe, the assistant and MCP, and a schedule or saved query that has no agent of its own.

The default can be a static agent or an elastic one. A stopped elastic default is started the way naming it would
start it, and the work waits for it. If it is already starting, the work waits for that start instead of beginning a
second one.

The default is strict. DuckHaven never runs the work on another agent, and never adds a pool agent for a workspace
that has a default. When the default cannot take the work, the request fails with `default_compute_unavailable`, and
the message names the agent and says why:

| Reason | Meaning | Status |
|---|---|---|
| `stopped`, `starting`, `stopping` | An elastic default the request may not start, or that is shutting down | 503, retry |
| `disconnected` | A static agent that is offline, or an elastic one that lost its connection while running | 503 |
| `elastic_disabled` | An elastic default while elastic compute is off | 503 |
| `start_failed` | The backend could not start it | 503 |
| `incompatible` | It lacks an extension a catalog needs, or its runtime cannot open a catalog | 422 |
| `runtime_retired` | Its runtime is retired, so it can never start | 422 |

The assistant and MCP never add a pool agent, with or without a default. They can start a stopped default. With no
default and nothing connected, they get `no_compute_available`. Any API caller can ask for the same by sending
`scale_out: false` with the query.

Clearing the default restores the pool behaviour described above.

### Watching the wait

A cold start is time work spends waiting for an agent, and these [metrics](../operations/monitoring.md) show it:

- `duckhaven_query_compute_wait_seconds` times work from submission to the moment an agent takes it. `origin` is the
  kind of run: `interactive` for a worksheet or API call, `elastic` for a pool run, `describe`, `scheduled` or
  `sql_session`. `outcome` is `bound` or `failed`. It includes the cold start, which
  `duckhaven_query_queue_wait_seconds` (the agent's admission queue) does not.
- `duckhaven_queries_parked` and `duckhaven_queries_parked_oldest_age_seconds` show what is waiting at each Prometheus
  scrape. `kind` is `pool` (any pool agent may take it), `targeted` (waiting for one named agent, such as a default
  compute) or `scheduled`.
- `duckhaven_stranded_work_failed_total` counts what the reaper gave up on, and `duckhaven_compute_unavailable_total`
  counts refusals by `reason`: the default compute's state, or `no_compute` when the workspace has none.
- `duckhaven_parked_queries_rejected_total` counts queries refused because a cap was reached; `scope` is `workspace` or
  `global`. A steady rate means more work is waiting for compute than the limits allow.

### Scaling in

A background reaper (leader-elected, like the scheduler and session reapers) terminates an elastic
agent when it has had **no work for the idle timeout** *and* has **no in-flight queries and no open
[SQL sessions](../querying/sql-sessions.md)** — an idle clock alone never tears down an agent that is mid-work. A
**max-lifetime** backstop bounds long-lived agents once their work drains.

## Lifecycle

An elastic agent's `lifecycle` is distinct from its socket *presence* (a running agent can briefly
disconnect without being torn down):

| State | Meaning |
|---|---|
| `provisioning` | The container is being created; the agent has not dialed home. |
| `running` | The agent has registered and is serving work. |
| `terminating` → `terminated` | The reaper is tearing the agent down (idle or max-lifetime). |
| `failed` | Provisioning never completed within the deadline, or the instance vanished. |

Because that column is mutated in place — a restart reuses the same agent row — it only ever
describes the agent as it is. Every transition is therefore also appended to a separate lifecycle
trail, together with the reason it happened (`idle`, `max_lifetime`, `provisioning_timeout`,
`restart`, `orphan`, `dead_row`). That trail is what the **Timeline** on the agent's
[monitoring page](agent-monitoring.md#the-charts) is drawn from, and it is the
only record that survives a restart — without it, an agent that has been torn down and brought
back has no history at all.

The same trail is what lets the timeline distinguish *not running* from *no record*: an agent
older than the trail has no recorded history, which is a weaker claim than knowing it was off.
An agent that vanished without a clean disconnect is closed out at its last heartbeat
(`presence_lost`), and one whose replica shut down is recorded as `replica_shutdown`.

The reasons the reaper itself tears an agent down for — `idle`, `max_lifetime`,
`provisioning_timeout`, `orphan`, `dead_row` — are the same strings it counts by in
`duckhaven_agents_reaped_total`, so a Prometheus alert and the UI cannot tell different stories
about why the reaper removed an agent. The trail's other reasons record events it did not cause.

## Reliability

Postgres is the single state-of-record: the agent row is written **before** the cloud instance is
created, so a crash mid-provision always leaves a reconcilable record. The reaper reconciles the
cloud against Postgres each cycle — a cloud instance with no live row is terminated (leak cleanup),
and a row whose instance has vanished is failed. The cloud is never a second source of truth.

## Related

- [Agents](agents.md) — the unit of compute elastic provisioning starts.
- [Elastic compute on a single Docker host](elastic-docker.md) — enabling it on
  the box already running your stack.
- [Elastic compute on Azure](elastic-azure.md) — enabling it against Container
  Instances.
- [Query execution](../querying/query-execution.md) — how an agent admits and runs a query.
