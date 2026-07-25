# Elastic compute on Azure

This page enables [elastic compute](../concepts/elastic-compute.md) with the **Azure Container
Instances (ACI)** backend, provisioning agents into **your own Azure subscription**.

!!! note "Scope"
    This is the first-party setup: DuckHaven provisions agent container groups into a resource group
    you own, using an identity you control. Bring-your-own-cloud (provisioning into a *customer's*
    resource group via delegated permissions) is planned but not yet shipped.

## Prerequisites

- An Azure subscription and a resource group dedicated to elastic agents.
- The control plane running with an Azure identity available to
  [`DefaultAzureCredential`](https://learn.microsoft.com/azure/developer/python/sdk/authentication/credential-chains)
  — a managed identity when the control plane runs in Azure, or `AZURE_*` environment variables
  otherwise.
- The agent image reachable from ACI (the default public image works out of the box).

## 1. Create the resource group and grant access

Create a resource group and give the control plane's identity permission to manage container groups
in it. Least privilege is a **custom role** scoped to that resource group; the built-in `Contributor`
role also works for a first pass.

```bash
az group create --name duckhaven-agents --location eastus

# Custom role: manage container groups only, scoped to the resource group.
az role definition create --role-definition '{
  "Name": "DuckHaven Elastic Agents",
  "AssignableScopes": ["/subscriptions/<sub>/resourceGroups/duckhaven-agents"],
  "Actions": [
    "Microsoft.ContainerInstance/containerGroups/read",
    "Microsoft.ContainerInstance/containerGroups/write",
    "Microsoft.ContainerInstance/containerGroups/delete"
  ]
}'

az role assignment create \
  --assignee <control-plane-identity-object-id> \
  --role "DuckHaven Elastic Agents" \
  --scope /subscriptions/<sub>/resourceGroups/duckhaven-agents
```

## 2. Configure the control plane

Set these on the API (environment variables shown; they map to the matching settings):

```bash
ELASTIC_COMPUTE_ENABLED=true
ELASTIC_PROVIDER=azure_aci
# Where a provisioned agent dials home. Elastic provisioning has no HTTP request
# to derive this from, so it must be configured explicitly.
ELASTIC_CONTROL_PLANE_URL=wss://duckhaven.example.com/agents/connect

ELASTIC_AZURE_SUBSCRIPTION_ID=<sub>
ELASTIC_AZURE_RESOURCE_GROUP=duckhaven-agents
ELASTIC_AZURE_LOCATION=eastus
ELASTIC_AZURE_CPU=2
ELASTIC_AZURE_MEMORY_GB=8
```

Tuning knobs (all optional, sensible defaults):

| Setting | Default | Purpose |
|---|---|---|
| `ELASTIC_IDLE_TIMEOUT_S` | `900` | Idle time before an agent is scaled in. |
| `ELASTIC_MAX_LIFETIME_S` | `14400` | Hard lifetime backstop once work drains. |
| `ELASTIC_PROVISIONING_DEADLINE_S` | `300` | Fail an agent that never dials home in this window. |
| `ELASTIC_MAX_AGENTS_PER_POOL` | `1` | Cap on concurrent elastic agents per storage shape (cost guard). |
| `ELASTIC_REAPER_TICK_S` | `30` | How often the scale-in / reconciliation loop runs. |

### Pricing (shown in the admin UI)

The **New compute** dialog shows each named size's hourly cost, computed from these rates. Override
them for your region or negotiated pricing:

| Setting | Default | Purpose |
|---|---|---|
| `ELASTIC_AZURE_PRICE_VCPU_HOUR` | `0.0486` | Per-vCPU hourly rate. |
| `ELASTIC_AZURE_PRICE_MEMORY_GB_HOUR` | `0.0054` | Per-GiB hourly rate. |
| `ELASTIC_CURRENCY` | `USD` | Currency label shown alongside prices. |

## 3. Verify

1. Open **Admin → Agents → New compute**, pick a size (its hourly cost is shown), and create it — a
   container group should appear in the resource group and register within ~tens of seconds. Or, run
   a query against the **elastic pool** (target the pool rather than a specific agent). With no
   agent connected, the run is accepted as `queued`.
2. Within ~tens of seconds a new container group appears in the `duckhaven-agents` resource group,
   the agent registers (**Admin → Agents** shows it `healthy`), and the queued run executes.
3. Leave it idle past `ELASTIC_IDLE_TIMEOUT_S`; the reaper terminates the container group and marks
   the agent `terminated`. No orphaned group should remain in the resource group.

## Operations

- **Leaks.** The reaper reconciles the resource group against DuckHaven's records every cycle: any
  `duckhaven-managed` container group with no live agent row is terminated, and any agent whose
  instance has vanished is failed. A stuck run is bounded by the provisioning deadline.
- **Cost.** The per-pool cap and the idle/max-lifetime timeouts bound spend. Alert on the
  provisioning-error rate.

## Related

- [Elastic compute](../concepts/elastic-compute.md) — the concept and lifecycle.
- [Add an agent](add-agent.md) — the static, manual equivalent.
