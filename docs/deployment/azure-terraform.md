# Azure with Terraform

Deploys DuckHaven to Azure as managed infrastructure: the control plane on Container
Apps, a managed PostgreSQL server, ADLS Gen2 for Iceberg data, and the plumbing that
lets the control plane create [elastic agents](../concepts/elastic-compute.md) on
demand. The Terraform lives in `deploy/terraform/`.

This is the cloud counterpart to the [Docker Compose install](install.md). Compose is
still the right choice for a single host; this is for a deployment that has to survive
a node failure and be reproducible from source.

!!! note "Not yet exercised end to end"
    Every phase of this deployment is verified by `terraform validate`, TFLint and a
    `terraform plan`, and the platform behaviour it relies on was confirmed with a
    throwaway spike. It has not yet been applied to a subscription from start to
    finish. Treat the first apply as a bring-up, and read
    [Before the first apply](#before-the-first-apply).

## What the network looks like

The design goal is a single public surface. Only the API is reachable from the
internet; everything else is private or internal.

```text
                        Internet
                           │  HTTPS + WSS (platform-managed certificate)
                           ▼
   ┌──────────────────────────────────────────────────────┐
   │ Container Apps environment (VNet-injected, zonal)    │
   │   api      external ingress :8000                    │
   │   polaris  internal ingress :8181                    │
   └────────┬──────────────────────────────┬──────────────┘
  agent     │  http :8001, intra-VNet      │  private endpoints
  subnet    ▼                              ▼
   ┌──────────────────────┐    ┌─────────────────────────────┐
   │ dh-agent-*           │    │ PostgreSQL Flexible Server  │
   │ private address only │───▶│ ADLS Gen2 (Iceberg)         │
   │ NAT gateway egress   │    │ Key Vault                   │
   └──────────────────────┘    └─────────────────────────────┘
```

| Endpoint | Public | Reachable from |
|---|---|---|
| DuckHaven API and UI | **yes** | the internet |
| Agent dial-home (WSS) | **yes** | agents, through the NAT gateway |
| Container registry | **yes**, credential-gated | Container Apps, container instances, CI |
| Polaris | no | the Container Apps and agent subnets |
| PostgreSQL, storage, Key Vault | no | private endpoints |
| Agent result servers | no | the Container Apps subnet only |

Two of those deserve explanation, because both are forced by the platform rather than
chosen.

**Agents have no public address.** Each is a container group injected into a delegated
subnet. Azure Container Instances offers either a public address with a DNS label *or*
subnet injection with a private address — never both — so this also means the control
plane fetches result data over the virtual network rather than the internet. It is why
the subnet needs a NAT gateway: an agent still has to reach the API's public ingress to
register, and Azure has retired default outbound access.

**The container registry stays public.** Container Instances pulls images from its own
control plane, outside the virtual network, so a registry restricted to a private
endpoint is rejected before a container is ever scheduled. The registry therefore keeps
its public endpoint with the admin user disabled: Container Apps pulls with a managed
identity, and container instances use a repository-scoped, pull-only token.

## Identity

Nothing in the running system holds a cloud credential. Two user-assigned managed
identities do the work:

- **The API** pulls its image, reads its secrets, signs storage URLs for SQL-session
  staging, and creates and deletes agent container groups. Its rights over agents come
  from a custom role — container group read/write/delete plus subnet join — granted at
  the agent resource group and the agent subnet, not at the subscription.
- **Polaris** pulls its image, reads its secrets, and mints the storage credentials it
  vends to agents.

Five secrets have to exist and live in Key Vault, referenced by the apps rather than
copied into their configuration: the database password, the app `SECRET_KEY`, the
inter-replica shared secret, the Polaris root credential, and the registry pull token.

## Region

The default is France Central rather than Germany West Central. PostgreSQL Flexible
Server is offer-restricted in Germany West Central on some subscriptions — the
capability API reports no available server editions, and the same applies to West
Europe, East US and East US 2. Check before choosing a region:

```sh
az postgres flexible-server list-skus -l <region> -o json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; print(d["reason"] or "available")'
```

A restriction can be lifted by a support request under *Service and subscription
limits*. Otherwise pick a supported region: `location` and `location_short` are the only
values that change.

## Before the first apply

1. **Terraform state.** The `bootstrap/` stack creates the storage account holding
   remote state. Run it once per subscription, then fill its output into
   `envs/<env>.backend.hcl`.
2. **An address the storage and vault firewalls will accept.** Creating the warehouse
   filesystem and writing secrets are data-plane calls, and both firewalls deny by
   default. Set `management_plane_allowed_ips` to the runner's egress address, or run
   from inside the virtual network with
   `allow_management_plane_public_access = false`. A plan-time precondition explains
   this rather than letting the apply fail halfway.
3. **Images in the registry.** A Container App whose image cannot be pulled never
   becomes healthy. Build and push `duckhaven-api` and `duckhaven-agent`, and mirror
   `apache/polaris` and `apache/polaris-admin-tool` at the tag `polaris_image_tag`
   names — both must be the same version, because the admin tool owns the schema the
   server reads.
4. **Provider registration.** A fresh subscription may spend several minutes
   registering resource providers on the first apply. Let it finish.

Then bootstrap the catalog and the first admin:

```sh
az containerapp job start -n polaris-bootstrap -g "$(terraform output -raw resource_group_name)"
```

The job creates the Polaris realm and root principal. It is safe to re-run — an
already-bootstrapped realm is treated as success. Finally read the one-time setup token
from the API replica and create the first admin, then register
`terraform output -raw warehouse_root_uri` as an `adls_gen2` storage backend with
`hierarchical: true`. `deploy/terraform/README.md` has the exact commands.

## Environments and cost

One root module serves every environment, selected by a `.tfvars` file and a state key,
rather than a directory per environment — a fix applied to one environment cannot then
miss another.

`envs/staging.tfvars` is tuned for a trial subscription: burstable PostgreSQL at the
storage floor with no standby, a Basic registry, single small replicas, no NAT gateway,
and a cap on log ingestion. Two things about it are worth stating plainly. A Basic
registry has no repository-scoped tokens, so agents there pull with the registry admin
user — acceptable only because that registry is disposable. And with no NAT gateway
elastic compute cannot work at all, so it stays disabled until the gateway is turned on;
the configuration rejects that combination rather than letting agents fail to register.

The largest saving is not a SKU choice. Everything bills for existing rather than for
being used, so destroying an environment between test sessions saves more than any
sizing decision. `az postgres flexible-server stop` pauses compute for up to 7 days if
the data needs keeping.

## Operating it

- Logs and metrics from every resource go to one Log Analytics workspace, bound to the
  Container Apps environment at creation.
- Alerts cover the failures that are otherwise invisible: PostgreSQL storage and CPU,
  elastic provisioning failures, and agents outliving their maximum lifetime. They are
  created only when `alert_email_addresses` is set — an alert nobody reads trains people
  to ignore the channel.
- `terraform destroy` does not remove leaked `dh-agent-*` container groups, because they
  are created at runtime and are not in state. Delete the agent resource group if any
  remain.
- Key Vault, storage account and registry names stay reserved during their soft-delete
  window. Change `name_suffix` to recover from a destroy/apply collision.

## Related

- [Elastic compute on Azure](azure-elastic-setup.md) — the agent-provisioning
  configuration in detail.
- [Configuration reference](../reference/configuration.md) — every environment variable.
- [High availability](high-availability.md) — the multi-replica control plane.
- [Docker Compose install](install.md) — the single-host alternative.
