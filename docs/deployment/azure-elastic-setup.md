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
- A **virtual network subnet delegated to `Microsoft.ContainerInstance/containerGroups`**, with an
  outbound route (a NAT gateway). Agents are placed in this subnet — see
  [Agent networking](#agent-networking) below.
- The agent image reachable from ACI (the default public image works out of the box).

## Agent networking

Each agent is created as a **subnet-injected container group with a private address and no public
DNS name**. Nothing about an agent is reachable from the internet; the control plane reaches its
result server over the virtual network.

Two consequences are worth understanding before you configure this:

- **The subnet needs its own outbound route.** An agent dials the control plane at whatever
  `ELASTIC_CONTROL_PLANE_URL` points at, and Azure has retired default outbound access for new
  deployments. Without a NAT gateway (or equivalent) on the subnet, an agent provisions, never
  registers, and is failed at `ELASTIC_PROVISIONING_DEADLINE_S`.
- **The control plane must share the network.** It fetches result data directly from each agent, so
  it has to be able to route to the subnet — running in the same virtual network, a peered one, or
  otherwise connected.
- **The catalog must be reachable from the subnet.** An agent attaches catalogs against Polaris
  itself, at `ELASTIC_AGENT_POLARIS_BASE_URL`, so whatever that points at has to resolve and answer
  from inside the agent subnet. On Container Apps that is a real constraint, not a formality: only
  replicas *inside* an environment can resolve an app with internal-only ingress, so an agent
  resolves such a hostname to the environment's public address and gets a 404. See
  [Azure with Terraform](azure-terraform.md) for how that deployment handles it.

Container Instances offers *either* a public address with a DNS label *or* subnet injection with a
private address, never both, so this is not configurable: agents are always private.

!!! note "Restrict inbound to the control plane"
    An agent's result server listens on port 8001 and is protected by a bearer token, but it has no
    other reason to accept connections. A network security group on the agent subnet that allows
    inbound 8001 only from the control plane's subnet is worth adding.

## 1. Create the resource group and grant access

Create a resource group and give the control plane's identity permission to manage container groups
in it. Least privilege is a **custom role** scoped to that resource group; the built-in `Contributor`
role also works for a first pass.

The identity needs two things: managing container groups in the resource group, and joining them to
the agent subnet.

```bash
az group create --name duckhaven-agents --location eastus

# Custom role: manage container groups, and place them in a subnet.
az role definition create --role-definition '{
  "Name": "DuckHaven Elastic Agents",
  "AssignableScopes": [
    "/subscriptions/<sub>/resourceGroups/duckhaven-agents",
    "/subscriptions/<sub>/resourceGroups/<network-rg>/providers/Microsoft.Network/virtualNetworks/<vnet>"
  ],
  "Actions": [
    "Microsoft.ContainerInstance/containerGroups/read",
    "Microsoft.ContainerInstance/containerGroups/write",
    "Microsoft.ContainerInstance/containerGroups/delete",
    "Microsoft.Network/virtualNetworks/subnets/read",
    "Microsoft.Network/virtualNetworks/subnets/join/action"
  ]
}'

az role assignment create \
  --assignee <control-plane-identity-object-id> \
  --role "DuckHaven Elastic Agents" \
  --scope /subscriptions/<sub>/resourceGroups/duckhaven-agents

# The same role at the subnet, for the join.
az role assignment create \
  --assignee <control-plane-identity-object-id> \
  --role "DuckHaven Elastic Agents" \
  --scope <subnet-resource-id>
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
# The delegated subnet agents are placed in, as a full resource id. Required.
ELASTIC_AZURE_SUBNET_ID=/subscriptions/<sub>/resourceGroups/<network-rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>
ELASTIC_DEFAULT_CPU=2
ELASTIC_DEFAULT_MEMORY_GB=4
```

If the agent image lives in a private registry, point DuckHaven at a user-assigned managed identity
that holds `AcrPull` on it. Each container group is created carrying that identity and pulls its
image as itself, so there is no registry password to store or rotate — and none appears in the
container group's specification, which anyone with reader access to the resource group can read.

```bash
ELASTIC_REGISTRY_SERVER=<registry>.azurecr.io
ELASTIC_REGISTRY_IDENTITY_ID=/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ManagedIdentity/userAssignedIdentities/<name>
```

Container Instances supports **user-assigned** identities only for image pull; a system-assigned
identity will not work. The control plane's own identity additionally needs
`Microsoft.ManagedIdentity/userAssignedIdentities/assign/action` on that identity, or provisioning
fails authorization before it reaches the pull.

!!! warning "The registry must stay publicly reachable"
    Container Instances pulls images from its own control plane, outside your virtual network, so a
    registry restricted to a private endpoint is rejected before the container is scheduled. Keep
    the registry's public endpoint enabled; access is gated by the identity above, not by the
    network.

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
| `ELASTIC_AZURE_PRICE_CURRENCY` | `USD` | The currency the two rates above are quoted in. |

## 3. Verify

1. Open **Compute → New compute**, pick a size (its hourly cost is shown), and create it — a
   container group should appear in the resource group and register within ~tens of seconds. Or, run
   a query against the **elastic pool** (target the pool rather than a specific agent). With no
   agent connected, the run is accepted as `queued`.
2. Within ~tens of seconds a new container group appears in the `duckhaven-agents` resource group,
   the agent registers (**Compute** shows it `healthy`), and the queued run executes.
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
