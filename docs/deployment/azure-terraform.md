# Azure with Terraform

Deploys DuckHaven to Azure as managed infrastructure: the control plane on Container
Apps, a managed PostgreSQL server, ADLS Gen2 for Iceberg data, and the plumbing that
lets the control plane create [elastic agents](../concepts/elastic-compute.md) on
demand. The Terraform lives in `deploy/terraform/`.

This is the cloud counterpart to the [Docker Compose install](install.md). Compose is
still the right choice for a single host; this is for a deployment that has to survive
a node failure and be reproducible from source.

!!! note "Maturity"
    The topology has been applied and exercised end to end on a staging subscription: the
    API serves, Polaris answers, a storage-backend health check vends credentials and
    reaches ADLS over its private endpoint, and an elastic agent has been created,
    queried, terminated and restarted. The passwordless database and registry paths are
    newer and have not yet been through a full apply. The first apply for any environment
    is staged — read [Before the first apply](#before-the-first-apply).

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

### Bringing your own server

Set `postgres_existing_server_fqdn` to point at a server you already run. The stack then
creates no server, no databases, no private endpoint and no bootstrap job, and those
become yours to provide:

- both databases must exist (`postgres_database_name` and
  `postgres_polaris_database_name`),
- the server must be reachable from the Container Apps subnet,
- Microsoft Entra authentication must be enabled, with a login role for the API's
  identity — `id-duckhaven-api-<environment>` — holding `CONNECT` on the DuckHaven
  database and `USAGE, CREATE` on its `public` schema, since the API runs its own
  migrations.

`api/src/api/tools/db_bootstrap.py` is the same script the managed path runs, so it can
be used by hand against your server if it has an Entra administrator you can act as.

## Bringing your own catalog

Polaris is deployed by default, but DuckHaven only needs *a* catalog — not this one.
Set `polaris_enabled = false` and `polaris_external_base_url` to point at a Polaris you
already run. The app, its bootstrap job, its identity, its four role assignments and its
database all disappear; the credential DuckHaven authenticates with is still generated
here and kept in Key Vault, so the matching principal has to exist in your Polaris under
`polaris_client_id`.

The catalog has to be reachable from the Container Apps environment and, when elastic
compute is on, from the agent subnet. Both leave through this deployment's NAT gateway,
so a single firewall rule for that address covers everything on your side.

Because Polaris is also the only component that needs a database password, turning it
off frees you to set `postgres_password_auth_enabled = false` and run the server on
Microsoft Entra authentication alone.

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
   remote state. Run it once per subscription, then fill its output into your root's
   backend config.
2. **An address the storage and vault firewalls will accept.** Creating the warehouse
   filesystem and writing secrets are data-plane calls, and both firewalls deny by
   default. Set `management_plane_allowed_ips` to the runner's egress address, or run
   from inside the virtual network with
   `allow_management_plane_public_access = false`. A plan-time precondition explains
   this rather than letting the apply fail halfway.
3. **Images in the registry.** A Container App whose image cannot be pulled never
   becomes healthy, so build and push `duckhaven-api` and `duckhaven-agent` between the
   registry apply and the app apply. The two Polaris images are mirrored by the apply
   itself, using a server-side `az acr import`; set `polaris_mirror_images = false` to
   do that yourself.
4. **Provider registration.** A fresh subscription may spend several minutes
   registering resource providers on the first apply. Let it finish.

Then run the two bootstrap jobs. Neither is part of the apply, because each must run
once against a database before the thing that uses it starts, and both are safe to
re-run:

```sh
RG="$(terraform output -raw resource_group_name)"
az containerapp job start -n db-bootstrap      -g "$RG"
az containerapp job start -n polaris-bootstrap -g "$RG"
```

`db-bootstrap` creates the API's Entra login role — **the API cannot start until it has
run**, since an identity that can authenticate still cannot log in without a database
role. `polaris-bootstrap` creates the Polaris realm and root principal.

Finally create the first admin with `terraform output -raw setup_token`, and register
`terraform output -raw warehouse_root_uri` as an `adls_gen2` storage backend with
`hierarchical: true`. `terraform output -raw next_steps` prints every one of these
commands with the names already filled in, and `deploy/terraform/Makefile` wraps the
sequence.

## Environments and cost

The deployment is a callable module, `deploy/terraform/modules/duckhaven-azure/`, with
two runnable roots beside it. `examples/production/` is the zone-redundant posture and
serves several environments from one root — selected by a `.tfvars` file and a state key,
rather than a directory each, so a fix applied to one environment cannot miss another.
`examples/quickstart/` is the cheapest configuration that works.

Quickstart drops SKUs and replica counts: burstable PostgreSQL at the storage floor with
no standby, LRS storage, a Basic registry, single small replicas, and a cap on log
ingestion. It does **not** drop the network — private endpoints, VNet injection and the
agent subnet are identical, because agents move real data over them.

Two things about it are worth stating plainly. The registry SKU costs nothing in
security: access is managed-identity authenticated on every SKU. And the NAT gateway is
a real functional limit rather than a saving — without it elastic compute cannot work at
all, so both stay off together, and the module rejects that combination rather than
letting agents fail to register.

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
