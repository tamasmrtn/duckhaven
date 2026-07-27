# DuckHaven on Azure — Terraform

Provisions DuckHaven on Azure Container Apps with managed PostgreSQL, ADLS Gen2, and
the infrastructure the API needs to create elastic agent container groups at runtime.

Only the DuckHaven API is reachable from the internet. Postgres, storage and Key Vault
sit behind private endpoints; elastic agents get private addresses in a delegated
subnet, with a network security group admitting only the Container Apps subnet to their
result port. Polaris uses internal-only ingress — except when elastic compute is
enabled, which forces a listener restricted to this deployment's own egress address.
The container registry is the other exception. Both are forced by Azure, not chosen; see
[the deviations](#three-documented-deviations).

**Nothing in the running system holds a password it could leak.** The API connects to
PostgreSQL with a Microsoft Entra token minted per connection, every image pull is
authenticated by a managed identity, and the storage account has no account keys at all.
One password remains, for Polaris, because its Quarkus datasource has no Entra path —
see [Authentication](#authentication).

> **Maturity:** the topology has been applied and exercised end to end on a staging
> subscription — API reachable, Polaris answering, a storage-backend health check
> vending credentials and reaching ADLS over its private endpoint, and an elastic
> agent created, queried and terminated. The passwordless database and registry paths
> are newer and have not yet been through a full apply. The first apply for any
> environment is [staged](#the-first-apply-is-staged).

## Prerequisites

- Terraform >= 1.9 (`sudo pacman -S terraform`, or download from HashiCorp).
- Azure CLI, logged in to the target subscription (`az login`). The apply itself uses it
  to mirror the Polaris images (`polaris_mirror_images`), and the bootstrap jobs are
  started with it.
- `ARM_SUBSCRIPTION_ID` exported, or an active CLI subscription. There is no
  `subscription_id` variable — one less input, and it cannot disagree with the
  credentials the apply is running under.
- Permission to create resource groups, role assignments and a custom role
  definition in the subscription.
- TFLint >= 0.64 for linting, then `tflint --init` once to fetch the azurerm ruleset.
  Optional; the pre-commit hook skips when it is absent.

The first `apply` against a fresh subscription may spend several minutes registering
resource providers — `Microsoft.Network` alone took about 4.5 minutes during
validation. Let it finish.

## Layout

| Path | Purpose |
|---|---|
| `modules/duckhaven-azure/` | The deployment itself, as one callable module. One file per concern. |
| `modules/private-endpoint/` | Private endpoint + private DNS A records. Used for each privately-exposed service. |
| `examples/quickstart/` | Cheapest working configuration. Four inputs. |
| `examples/production/` | Production posture; one root serving several environments via `envs/`. |
| `bootstrap/` | One-time creation of the remote state backend. Keeps local state. |

The module declares no `provider` and no `backend` — those belong to whichever root
calls it, so that one deployment's state location and credentials are not baked into the
thing being reused. Each example supplies its own.

Individual Azure resources inside the module are not wrapped in further modules: each is
used exactly once, so a module would add indirection with no second caller. The
exception is `private-endpoint`, which has four.

Pick an example and apply it, or copy one as the starting point for your own root. The
two differ only in what they pass to the module — see each one's README.

## Naming

Every resource follows one pattern, based on the Cloud Adoption Framework's
[resource naming guidance][caf]:

[caf]: https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming

```text
<abbr>-<env>-<workload>-<region>-<suffix>        kv-prd-duckhaven-frc-wej
```

The resource-type abbreviation leads, so a resource group listing sorts by type. The
abbreviations are CAF's own — `rg`, `kv`, `st`, `cr`, `cae`, `ca`, `caj`, `pgsql`,
`vnet`, `snet`, `nsg`, `pip`, `ng`, `pep`, `id`, `log`, `ag`.

Two deviations, both forced by Azure:

**Storage accounts and container registries take no hyphens** — their names admit only
letters and digits. They use the same components concatenated: `stprdduckhavenfrcwej`.

**Resources scoped inside a parent that already names the workload drop that segment.**
Subnets live in `vnet-prd-duckhaven-frc-wej`; apps and jobs live in
`cae-prd-duckhaven-frc-wej`. So they are `snet-prd-aca-frc-wej` and
`ca-prd-api-frc-wej`. This is not only brevity: a container app's name is capped at 32
characters *and* becomes its public DNS label, which is the hostname your users see.

### Every component is fixed-length, because Key Vault is

`kv-prd-duckhaven-frc-wej` is exactly 24 characters, and 24 is the Key Vault maximum.
There is no headroom at all, which is why `environment`, `location_short` and
`name_suffix` are each validated to exactly three characters and why the workload name
is a constant rather than a variable.

The four names that could realistically overflow — Key Vault (24), the storage account
(24), and the two container apps and two jobs (32) — carry `precondition` blocks that
fail at plan time with the arithmetic, rather than letting Azure reject them partway
through an apply.

| Resource | Example | Limit |
|---|---|---|
| Key vault | `kv-prd-duckhaven-frc-wej` | 24 / **24** |
| Storage account | `stprdduckhavenfrcwej` | 20 / 24 |
| Container registry | `crprdduckhavenfrcwej` | 20 / 50 |
| PostgreSQL server | `pgsql-prd-duckhaven-frc-wej` | 27 / 63 |
| Container apps environment | `cae-prd-duckhaven-frc-wej` | 25 / 60 |
| Container app | `ca-prd-api-frc-wej` | 18 / 32 |
| Container app job | `caj-prd-polaris-boot-frc-wej` | 28 / 32 |
| Managed identity | `id-prd-duckhaven-api-frc-wej` | 28 / 128 |
| Virtual network | `vnet-prd-duckhaven-frc-wej` | 26 / 64 |
| Subnet | `snet-prd-aca-frc-wej` | 20 / 80 |
| Private endpoint | `pep-prd-st-blob-frc-wej` | 23 / 64 |

Names that are *not* on the convention are functional identifiers rather than inventory
keys: the two database names (`duckhaven`, `polaris`) and the ADLS filesystem
(`warehouse`) appear in application configuration, the private DNS zones must be exactly
`privatelink.*`, and the NSG rules are named for what they do.

## Authentication

Four user-assigned managed identities do all the work, and none of them holds a stored
credential.

| Identity | Holds | Used for |
|---|---|---|
| `id-<env>-duckhaven-api-<region>-<suffix>` | AcrPull, Key Vault Secrets User, Storage Blob Data Contributor + Delegator, the custom elastic-agent role | Pulling its image, reading secrets, connecting to Postgres, signing staging URLs, creating agent container groups |
| `id-<env>-duckhaven-polaris-<region>-<suffix>` | AcrPull, Key Vault Secrets User, Storage Blob Data Contributor + Delegator | Pulling its image, reading secrets, minting the SAS it vends to agents |
| `id-<env>-duckhaven-agent-<region>-<suffix>` | AcrPull | Carried by every provisioned container group; how it pulls its image |
| `id-<env>-duckhaven-dbadmin-<region>-<suffix>` | AcrPull, and Entra administrator *on the server* | One job, once, creating the API's login role |

**The database.** Azure Database for PostgreSQL accepts a Microsoft Entra access token in
the password field, so the API connects as its own identity and the driver mints a token
per connection (`api/src/api/db/entra.py`). Nothing is generated, vaulted, kept in state
or rotated — including for the Alembic migrations the API runs on start.

An identity that can authenticate still cannot *log in* without a database role, and
creating one means running `pgaadauth_create_principal` as an Entra administrator.
Registering the API's own identity as that administrator would remove the bootstrap job
entirely, at the cost of giving the internet-facing app `azure_pg_admin` over every
database on the server. The `dbadmin` identity holds it instead.

**Polaris is the exception.** Its Quarkus datasource would need
`azure-identity-extensions` on the pgjdbc classpath to present an Entra token, and the
stock `apache/polaris` image does not ship it. It keeps a generated password in Key
Vault, which is why `postgres_password_auth_enabled` defaults to true. Running without
Polaris lets you set it false and close password authentication off entirely.

**The registry.** No credential is ever issued: `admin_enabled` is false and there are no
scoped tokens. Container Apps pulls with its app identity; container instances carry the
agent identity and pull as themselves, which Azure supports for *user-assigned*
identities only. The control plane additionally needs
`Microsoft.ManagedIdentity/userAssignedIdentities/assign/action` on the agent identity to
attach it — that is in the custom role, and without it provisioning fails authorization
before it ever reaches the pull.

**Storage.** `shared_access_key_enabled` is false, so no account key exists to leak.
Polaris and the API both mint short-lived user-delegation SAS with their identities.

## First deployment

`make` wraps the ordered sequence; `ROOT` selects the example.

```sh
export ARM_SUBSCRIPTION_ID=<your subscription>

# 1. State backend (once per subscription). Copy storage_account_name into the
#    example's backend config.
cd deploy/terraform/bootstrap
terraform init && terraform apply -var storage_account_name=<globally-unique-name>
cd ..

# 2. The deployment.
make init      ROOT=examples/quickstart
make registry  ROOT=examples/quickstart   # see below: the first apply is staged
make images    ROOT=examples/quickstart TAG=v1.0.0
make apply     ROOT=examples/quickstart
make bootstrap ROOT=examples/quickstart
make outputs   ROOT=examples/quickstart   # prints what is left to do
```

`examples/production` is the same, with `BACKEND=envs/prd.backend.hcl` and
`VARS=envs/prd.tfvars`; switching environments there needs `terraform init -reconfigure`.

The plain Terraform commands are in each example's README if you would rather not use
`make`.

## The first apply is staged

A Container App whose image cannot be pulled never becomes healthy, and the images can
only be pushed once the registry exists — so the first apply for an environment runs in
two passes. Later applies are a single `make apply`.

`-target` is a documented troubleshooting tool rather than a workflow, and this is the
one place it earns its keep: there is no way to express "create this resource first"
across an apply boundary. `make registry` is that one command.

```sh
terraform apply -target=module.duckhaven.azurerm_container_registry.main
```

Optionally, apply the slow resources in the first pass too so they build while the
images do — PostgreSQL takes about ten minutes and the Container Apps environment about
four:

```sh
terraform apply \
  -target=module.duckhaven.azurerm_container_app_environment.main \
  -target=module.duckhaven.azurerm_storage_data_lake_gen2_filesystem.warehouse \
  -target=module.duckhaven.azurerm_postgresql_flexible_server_database.duckhaven \
  -target=module.duckhaven.azurerm_postgresql_flexible_server_database.polaris
```

## Images must be in the registry before the apps are created

Terraform references images by digest-less tag inside your own registry. A Container App
whose image cannot be pulled never becomes healthy, and the apply either fails or leaves
a broken revision, so populate the registry between the registry apply and the app
apply.

```sh
make images ROOT=examples/quickstart TAG=v1.0.0
```

The two Polaris images are mirrored **by the apply itself** (`az acr import`, run
server-side, so nothing is pulled or pushed by the runner), which is why they are no
longer a manual prerequisite. It needs the Azure CLI on the runner; set
`polaris_mirror_images = false` to do it yourself:

```sh
az acr import -n "$ACR" --source docker.io/apache/polaris:1.6.0            --image polaris:1.6.0
az acr import -n "$ACR" --source docker.io/apache/polaris-admin-tool:1.6.0 --image polaris-admin-tool:1.6.0
```

Both must be the same version — the admin tool owns the schema the server reads.

## Bootstrapping

Two manual-trigger jobs, neither part of the apply, because each must run once against a
database before the thing that uses it starts. Both are safe to re-run.

```sh
make bootstrap ROOT=examples/quickstart
```

- **The database job** (`caj-<env>-db-boot-…`) creates the API's Microsoft Entra login role. **The API cannot
  start until this has run** — an Entra identity can authenticate to the server but
  cannot log in without a database role, so replicas fail their startup probe until it
  exists. Terraform can guarantee the job exists, not that it has been run.
- **The Polaris job** (`caj-<env>-polaris-boot-…`) creates the Polaris realm and root principal. The admin tool
  exits 3 when the realm already exists, and the job's command treats that as success —
  the same contract as `deploy/polaris-bootstrap.sh`.

## Creating the first admin

`terraform output -raw next_steps` prints every remaining command with the resource
group, registry and URL already filled in. The first-admin step is:

```sh
curl -sS -X POST "$(terraform output -raw api_url)/api/setup/admin" \
  -H "X-Setup-Token: $(terraform output -raw setup_token)" \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"...","name":"Admin"}'
```

The token is generated by Terraform and injected from Key Vault, rather than written to
the container filesystem on first boot — that filesystem is ephemeral here, so a
self-generated token changes whenever a replica is replaced and cannot be read once and
then spent. Creating a first admin is refused outright once any user exists, which is
what bounds replay.

Then register the warehouse as an `adls_gen2` storage backend, using
`terraform output -raw warehouse_root_uri` as the root URI with `hierarchical: true` and
the subscription's tenant id, and run **Test access**. A `valid: true` result means
Polaris vended a SAS and reached the account over the private endpoint.

## Linting

```sh
cd deploy/terraform
tflint --init                                    # once, fetches the azurerm ruleset
TFLINT_CONFIG_FILE="$PWD/.tflint.hcl" tflint --recursive
```

**`TFLINT_CONFIG_FILE` must be absolute, and it matters.** With `--recursive` tflint
changes into each subdirectory and looks for a `.tflint.hcl` *there*. Without the
environment variable, `bootstrap/`, `modules/` and `examples/` are linted with default
configuration — no azurerm plugin — and report a misleading clean result. The
pre-commit hook sets it for you.

Two rules are configured deliberately, both explained in `.tflint.hcl`:
`azurerm_resource_missing_tags` is switched **on** to enforce the tagging convention,
and `azurerm_resources_missing_prevent_destroy` is switched **off** because deletion
protection is enforced Azure-side (purge protection, soft delete, PITR) and
`lifecycle` blocks cannot be parameterised per environment — setting it would make a
disposable environment undestroyable.

## Three documented deviations

**Not every region can host this.** PostgreSQL Flexible Server is offer-restricted in
some regions on some subscriptions: the capability API reports `OfferRestricted` and
*"Provisioning is restricted in this region"*, and the apply fails several minutes in.
Check with `az postgres flexible-server list-skus -l <region>` before setting
`location`, and prefer a region with three availability zones so zone-redundant HA and
ZRS storage are available. Microsoft can grant a regional exception via a *Service and
subscription limits* support request.

`location` has no default for this reason, and `location_short` — which appears in
every resource name — is derived from it via a lookup in `locals.tf`. A region that
lookup does not cover fails at plan time, telling you to set `location_short`.

**The container registry keeps a public endpoint.** Azure Container Instances pulls
images from its own control plane, outside the VNet. A network-restricted registry is
rejected at ARM pre-flight with `InaccessibleImage`, even when the container group is
injected into a subnet that resolves the registry's private endpoint. The registry
therefore stays publicly reachable. Everything else is private: a VNet-injected
container group *does* resolve privatelink DNS and reach private endpoints.

Public does not mean open. The admin user is disabled and no registry credential is
ever issued — every pull is authenticated by a managed identity holding `AcrPull`: the
app identities for Container Apps, and a dedicated agent identity that each container
group carries and pulls as. `acr_sku` is therefore a cost decision, not a security one.

**Enabling elastic compute exposes Polaris.** Only replicas *inside* a Container Apps
environment can resolve an internal-ingress app. An agent runs in its own subnet,
outside the environment, so it resolves Polaris' internal hostname to the environment's
*public* address and gets a 404 — measured from the agent subnet. A private endpoint
cannot bridge it either: Azure rejects one unless the environment's public network
access is disabled, which would take the API offline with it.

So when `elastic_compute_enabled` is true, Polaris switches to external ingress with an
allow rule for exactly one address: this deployment's NAT gateway, which is the egress
for both the control plane and its agents. A Container Apps ingress with any allow rule
denies everything unmatched, so the listener is public but reachable only from our own
traffic — verified by getting 403 from elsewhere. With elastic compute off, Polaris has
no public listener at all.

If that is not acceptable, the fully-private alternative is a second, internal-only
Container Apps environment in the same VNet to host Polaris. That is a larger change and
is not implemented here.

## Cost

Figures below are rough order-of-magnitude monthly list prices to show *relative*
weight — check the Azure pricing calculator for real numbers. What matters is which
resources bill hourly whether or not anything uses them.

| Resource | `production` | `quickstart` | Notes |
|---|---|---|---|
| PostgreSQL | ~$155 | ~$16 | `GP_Standard_D2ds_v5` + zone-redundant HA + 128 GiB, versus Burstable `B1ms` + 32 GiB and no standby. HA roughly doubles compute. |
| Container registry | ~$50 | ~$5 | Premium versus Basic. |
| NAT gateway + public IP | ~$36 | $0 | Off in quickstart; bills hourly regardless of traffic. |
| Private endpoints | ~$29 | ~$29 | 4 × ~$7/mo. Deliberately *not* reduced — see below. |
| Private DNS zones | ~$2 | ~$2 | 4 × $0.50. |
| Storage account | pennies | pennies | Standard, small data volume. |
| Log Analytics | per GB | per GB | Capped at 1 GB/day in quickstart. |
| Container Apps | per vCPU-second | per vCPU-second | Production runs each app at 2 × 1 vCPU/2 GiB, quickstart at 1 × 0.5 vCPU/1 GiB. Always-on replicas bill continuously, at a reduced idle rate when serving no requests. Neither app can scale to zero. |

Three things are worth knowing:

**Destroying is the real lever.** Everything above bills for existing, not for being
used, and the whole environment is reproducible from one `terraform apply`. For a trial
subscription with a fixed credit, `terraform destroy` between sessions saves more than
any SKU choice. Postgres can also be paused for up to 7 days
(`az postgres flexible-server stop`) if you want to keep the data.

**Private networking is not a tier.** Private endpoints are the largest quickstart line
item after Postgres, and they are the same in both examples on purpose. Agents move and
query real data over these paths, so the isolation *is* the deployment — an environment
without it would neither be safe to put data in nor verify the topology that is. There
is deliberately no switch to turn it off.

**The registry SKU costs nothing in security.** Access is identical on Basic and
Premium — a managed identity holding `AcrPull`, no credential issued. What Basic gives
up is zone redundancy, network rule sets, retention policies, and included storage and
throughput.

`nat_gateway_enabled = false` is a real functional limitation rather than only a saving:
Azure has retired default outbound access, so a VNet-injected agent has no route to the
API's public ingress and cannot dial home. Turn it on for the session where elastic
compute is being exercised; the module refuses the combination outright rather than
letting agents fail to register.

## The Terraform runner needs data-plane access

Creating the warehouse filesystem and writing Key Vault secrets are data-plane calls,
and both firewalls deny by default. Set `management_plane_allowed_ips` to the runner's
egress address (`curl -s https://api.ipify.org`), or apply from inside the VNet with
`allow_management_plane_public_access = false`. A resource precondition fails the plan
with this explanation rather than letting the apply get halfway and 403.

## State contains secrets

Generated secrets (`SECRET_KEY`, the inter-replica secret, the Polaris root credential,
the setup token, and the Polaris database password) are `random_password` resources, so
they exist in state. The backend storage account is Entra-only (no account keys),
versioned and private. Treat read access to it as equivalent to read access to the
secrets.

The API's database credential is *not* among them: it authenticates with a managed
identity, so there is nothing to generate. Removing Polaris
(`polaris_enabled = false`) removes the last database password from state as well.

## Notes

- `snet-aca` CIDR is **immutable** once the Container Apps environment exists. It is
  sized `/23` up front.
- Key Vault, storage account and registry names stay reserved during their
  soft-delete window. Change `name_suffix` to recover from a destroy/apply collision,
  or `az keyvault purge`.
- Elastic agents get their own resource group. The reaper terminates every container
  group there tagged `duckhaven-managed=true` with no live agent row, so nothing else
  may share it.
- `terraform destroy` does not remove leaked `dh-agent-*` container groups — they are
  created at runtime and are not in state. Delete the agents resource group if any
  remain.
