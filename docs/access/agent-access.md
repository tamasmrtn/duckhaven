---
type: concept
---

# Agent access

Agent access decides who may run work on an agent and who may restart or administer it, so a team can reserve compute
while the rest of the fleet stays open.

[Scoped access](scoped-access.md) answers which *data* someone may reach; agent access answers which *compute* they may
run it on. On a shared [elastic fleet](../compute/elastic-compute.md) an agent has its own cost, blast radius and
closeness to sensitive data, so one team may reasonably own it and another should perhaps not be able to restart it.

!!! info "Availability"
    Every deployment. Changing an agent's access needs the `admin` tier on that agent, which `agents:manage` confers on
    every agent.

## Tiers

Each principal's access to each agent resolves to one tier, each including everything below it:

| Tier | Can do |
|---|---|
| `use` | Send queries, SQL sessions and scheduled jobs to the agent; see its status and monitoring page |
| `operate` | Everything `use` can, plus restart, terminate, force-disconnect it, and revoke its credential |
| `admin` | Everything `operate` can, plus delete the agent, change its access mode, and grant or revoke access to it |

Sending work to an [elastic](../compute/elastic-compute.md) agent that the idle reaper stopped **starts it**, and that
needs only `use`. It is dispatch, not a lifecycle operation: the caller chooses neither the agent's size nor when it
stops. Restarting an agent deliberately, from the **Compute** page or the API, needs `operate`.

Holding `agents:manage` gives `admin` on every agent. Agent access can add access for people without that permission,
but never takes it away from someone who has it. Creating compute and minting bootstrap tokens stay with
`agents:manage` alone: both are spending decisions about the deployment.

## Open and restricted agents

Each agent has an **access mode**:

- **`open`** (the default) — anyone signed in may use the agent.
- **`restricted`** — using it needs an explicit grant. To anyone without one the agent is invisible: it is missing from
  the engine picker and the agent list, and its pages report "not found" rather than "forbidden".

The mode affects only `use`. `operate` and `admin` always need a grant or `agents:manage`, on an open agent as much as
on a restricted one. On an open agent a grant can raise someone, to `operate` say, but nothing lowers anyone below
`use`: grants add up, and there is no negative grant.

When you create elastic compute, the **New compute** dialog asks who can use it. Choose **Only people I grant access**
for an agent meant to be reserved, rather than restricting it after it starts taking work.

## Granting to a person or a workspace

A grant names either a **user** — a person or a service account — or a **workspace**. A workspace grant reaches every
member and follows membership: add someone to the workspace and they gain the agent; remove them and they lose it. A
user's effective tier is the highest of their own grant and the grants on every workspace they belong to.

Use a workspace grant for "everyone in Analytics may run work on the shared ADLS agent", which would be stale the moment
the team changed if granted person by person. Use a personal grant for "Dana may restart it", which should name Dana.

A workspace grant is capped at `operate`. The `admin` tier includes granting access, and handing that to whoever is in a
workspace would let the set of people who can widen access change every time someone joined.

Manage grants from **Compute** → the agent → **Access**.

## Related

- [How access works](how-access-works.md) — how agent access fits with the other layers.
- [Agents](../compute/agents.md) — what an agent is and how work is routed to it.
- [Permissions reference](../reference/permissions.md#agent-tiers) — the agent tiers.
