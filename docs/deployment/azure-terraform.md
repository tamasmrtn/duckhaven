# Azure with Terraform

Deploys DuckHaven to Azure as managed infrastructure: the control plane on Container
Apps, a managed PostgreSQL server, ADLS Gen2 for Iceberg data, and the plumbing that
lets the control plane create [elastic agents](../concepts/elastic-compute.md) on
demand. The Terraform lives in `deploy/terraform/`.

This is the cloud counterpart to the [Docker Compose install](install.md). Compose is
still the right choice for a single host; this is for a deployment that has to survive
a node failure and be reproducible from source.

!!! note "Verified on a staging subscription"
    This has been applied end to end to a staging environment in France Central: the API
    serves, Polaris answers, a storage-backend health check vends credentials and reaches
    ADLS over its private endpoint, and an elastic agent has been created, queried,
    terminated and restarted. It has not been applied to a production environment. The
    first apply for any environment is staged — read
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
| Polaris | only with elastic compute, restricted to one address | the Container Apps and agent subnets |
| PostgreSQL, storage, Key Vault | no | private endpoints |
| Agent result servers | no | the Container Apps subnet only |

Three of those deserve explanation, because each is forced by the platform rather than
chosen.

**Agents have no public address.** Each is a container group injected into a delegated
subnet. Azure Container Instances offers either a public address with a DNS label *or*
subnet injection with a private address — never both — so this also means the control
plane fetches result data over the virtual network rather than the internet. It is why
the subnet needs a NAT gateway: an agent still has to reach the API's public ingress to
register, and Azure has retired default outbound access.

**Polaris is exposed once elastic compute is enabled.** Only replicas inside a Container
Apps environment resolve an internal-ingress app. An agent sits in its own subnet,
outside the environment, so it resolves Polaris' internal hostname to the environment's
public address and receives a 404. A private endpoint cannot bridge the gap either:
Azure refuses one unless the environment's public network access is disabled, which
would take the API down with it. Polaris therefore switches to external ingress with a
single allow rule — the deployment's NAT gateway, which is the egress address of both
the control plane and its agents — and a Container Apps ingress with any allow rule
denies everything else. Turning elastic compute off removes the listener entirely. The
fully-private alternative is a second, internal-only environment for Polaris, which this
deployment does not implement.

**The container registry stays public.** Container Instances pulls images from its own
control plane, outside the virtual network, so a registry restricted to a private
endpoint is rejected before a container is ever scheduled. The registry therefore keeps
its public endpoint — but issues no credentials. The admin user is disabled and every
pull is authenticated by a managed identity holding `AcrPull`, including the container
groups, which each carry an identity and pull as themselves.

## Identity

Nothing in the running system holds a cloud credential. Four user-assigned managed
identities do the work:

- **The API** pulls its image, reads its secrets, connects to PostgreSQL, signs storage
  URLs for SQL-session staging, and creates and deletes agent container groups. Its
  rights over agents come from a custom role — container group read/write/delete, subnet
  join, and permission to attach the agent identity — granted at the agent resource
  group, the agent subnet and that identity, not at the subscription.
- **Polaris** pulls its image, reads its secrets, and mints the storage credentials it
  vends to agents.
- **The agent identity** is carried by every provisioned container group and is how it
  pulls its image. It holds `AcrPull` on the registry and nothing else, so it grants
  strictly less than the control plane does.
- **The database bootstrap identity** is the server's Microsoft Entra administrator. It
  exists to run one job once and is never attached to a running app.

Three secrets have to exist and live in Key Vault, referenced by the apps rather than
copied into their configuration: the app `SECRET_KEY`, the inter-replica shared secret,
and the Polaris root credential. A fourth, the database password, exists only for
Polaris — see [Database access](#database-access).

## Database access

The API holds no database password. Azure Database for PostgreSQL accepts a Microsoft
Entra access token in the password field, so the API connects as its own managed
identity and the driver mints a token for each new connection — there is nothing to
generate, store in a vault, keep in Terraform state, or rotate. The same applies to the
Alembic migrations the API runs on start.

An Entra identity can authenticate to the server but cannot log in until a database role
exists for it, and creating one means running `pgaadauth_create_principal` as an Entra
administrator. Registering the API's own identity as that administrator would skip the
step entirely, at the cost of giving the internet-facing application `azure_pg_admin`
over every database on the server. Instead a separate bootstrap identity holds that
role, and a manual-trigger Container Apps job uses it once to create an ordinary login
role for the API. The job runs inside the environment because the server has no public
endpoint — a Terraform runner outside the virtual network cannot reach it.

Polaris is the exception, and password authentication stays enabled for it. Its Quarkus
datasource would need `azure-identity-extensions` on the pgjdbc classpath to present an
Entra token, and the stock `apache/polaris` image does not ship it. Its password is
generated by Terraform and lives in Key Vault. Set
`postgres_password_auth_enabled = false` to close that path off if you run without
Polaris.

## Region

Pick the region deliberately: PostgreSQL Flexible Server is offer-restricted in some
regions on some subscriptions — the capability API reports no available server editions,
and the apply fails several minutes in. Check before choosing:

```sh
az postgres flexible-server list-skus -l <region> -o json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; print(d["reason"] or "available")'
```

A restriction can be lifted by a support request under *Service and subscription
limits*. Prefer a region with three availability zones, so zone-redundant HA and ZRS
storage are available.

`location` has no default for this reason. `location_short`, which appears in every
resource name, is derived from it by a lookup table in `locals.tf`; a region that table
does not cover fails at plan time and tells you to set `location_short` yourself.

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
and a cap on log ingestion. The registry SKU costs nothing in security — access is
managed-identity authenticated on every SKU — but the NAT gateway is a real functional
limit: without it elastic compute cannot work at all, so it stays disabled until the
gateway is turned on, and the configuration rejects that combination rather than letting
agents fail to register.

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
