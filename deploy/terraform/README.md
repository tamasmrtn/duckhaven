# DuckHaven on Azure — Terraform

Provisions DuckHaven on Azure Container Apps with managed PostgreSQL, ADLS Gen2, and
the infrastructure the API needs to create elastic agent container groups at runtime.

Only the DuckHaven API is reachable from the internet. Postgres, storage and Key Vault
sit behind private endpoints; Polaris uses internal-only ingress; elastic agents get
private IPs in a delegated subnet.

> **Status:** phases 1-2 of 7. Networking, Log Analytics, Key Vault, PostgreSQL, the
> ADLS Gen2 warehouse and the container registry are in place. The Container Apps
> environment, Polaris and the API app land in later phases.

## Prerequisites

- Terraform >= 1.9 (`sudo pacman -S terraform`, or download from HashiCorp).
- TFLint >= 0.64 for linting, then `tflint --init` once to fetch the azurerm ruleset.
- Azure CLI, logged in to the target subscription (`az login`).
- Permission to create resource groups, role assignments and a custom role
  definition in the subscription.

The first `apply` against a fresh subscription may spend several minutes registering
resource providers — `Microsoft.Network` alone took about 4.5 minutes during
validation. Let it finish.

## Layout

| Path | Purpose |
|---|---|
| `bootstrap/` | One-time creation of the remote state backend. Keeps local state. |
| `*.tf` | The single root module, one file per concern. |
| `modules/private-endpoint/` | Private endpoint + private DNS A records. Used for each privately-exposed service. |
| `envs/<env>.tfvars` | Per-environment inputs. |
| `envs/<env>.backend.hcl` | Per-environment state location. |

There is one root module rather than a directory per environment, because duplicated
roots drift: a fix applied to prod silently misses staging. Environments differ only
by their `.tfvars` and their state key. Individual Azure resources are not wrapped in
modules — each is used exactly once, so a module would add indirection with no second
caller.

## First deployment

```sh
# 1. State backend (once per subscription).
cd deploy/terraform/bootstrap
terraform init
terraform apply \
  -var subscription_id=<sub> \
  -var storage_account_name=<globally-unique-name>
# Copy storage_account_name into envs/<env>.backend.hcl.

# 2. The deployment itself.
cd ..
terraform init -backend-config=envs/prod.backend.hcl
terraform apply -var-file=envs/prod.tfvars
```

Switching environments re-initialises the backend:

```sh
terraform init -reconfigure -backend-config=envs/staging.backend.hcl
terraform apply -var-file=envs/staging.tfvars
```

## Linting

```sh
cd deploy/terraform
tflint --init                                    # once, fetches the azurerm ruleset
TFLINT_CONFIG_FILE="$PWD/.tflint.hcl" tflint --recursive
```

**`TFLINT_CONFIG_FILE` must be absolute, and it matters.** With `--recursive` tflint
changes into each subdirectory and looks for a `.tflint.hcl` *there*. Without the
environment variable, `bootstrap/` and `modules/` are linted with default
configuration — no azurerm plugin — and report a misleading clean result. The
pre-commit hook sets it for you.

Two rules are configured deliberately, both explained in `.tflint.hcl`:
`azurerm_resource_missing_tags` is switched **on** to enforce the tagging convention,
and `azurerm_resources_missing_prevent_destroy` is switched **off** because deletion
protection is enforced Azure-side (purge protection, soft delete, PITR) and
`lifecycle` blocks cannot be parameterised per environment — setting it would make the
staging environment undestroyable.

## Two documented deviations

**Region is France Central, not Germany West Central.** PostgreSQL Flexible Server
cannot be provisioned in Germany West Central on this subscription — the capability
API reports `OfferRestricted` and *"Provisioning is restricted in this region"* (the
same applies to West Europe, East US and East US 2). France Central is the nearest
supported EU region with three availability zones, and offers `Standard_D2ds_v5` with
zone-redundant HA. Set `location` and `location_short` to move, if Microsoft grants a
regional exception via a *Service and subscription limits* support request.

**The container registry keeps a public endpoint.** Azure Container Instances pulls
images from its own control plane, outside the VNet. A network-restricted registry is
rejected at ARM pre-flight with `InaccessibleImage`, even when the container group is
injected into a subnet that resolves the registry's private endpoint. The registry
therefore stays publicly reachable with the admin user disabled, Container Apps
pulling via managed identity and container instances via a repository-scoped,
pull-only token. Everything else is private: a VNet-injected container group *does*
resolve privatelink DNS and reach private endpoints.

The registry is **Premium**, which is what repository-scoped tokens require. On
Standard the only credential a container instance could use is the registry admin
user — push and pull across every repository — and that credential would live in the
API's environment and in every provisioned agent's container spec. Premium buys the
scoped, pull-only alternative.

## Cost, and the staging environment

Figures below are rough order-of-magnitude monthly list prices to show *relative*
weight — check the Azure pricing calculator for real numbers. What matters is which
resources bill hourly whether or not anything uses them.

| Resource | Prod | Staging | Notes |
|---|---|---|---|
| PostgreSQL | ~$155 | ~$16 | `GP_Standard_D2ds_v5` + zone-redundant HA + 128 GiB, versus Burstable `B1ms` + 32 GiB and no standby. HA roughly doubles compute. |
| Container registry | ~$50 | ~$5 | Premium versus Basic. See the trade below. |
| NAT gateway + public IP | ~$36 | $0 | Disabled in staging; bills hourly regardless of traffic. |
| Private endpoints | ~$29 | ~$29 | 4 × ~$7/mo. Deliberately *not* reduced — see below. |
| Private DNS zones | ~$2 | ~$2 | 4 × $0.50. |
| Storage account | pennies | pennies | Standard, small data volume. |
| Log Analytics | per GB | per GB | Capped at 1 GB/day in staging. |

Three things are worth knowing:

**Destroying staging is the real lever.** Everything above bills for existing, not for
being used, and the whole environment is reproducible from one `terraform apply`. For a
trial subscription with a fixed credit, `terraform destroy` between test sessions saves
more than any SKU choice. Postgres can also be paused for up to 7 days
(`az postgres flexible-server stop`) if you want to keep the data.

**Private endpoints are still the largest staging line item after Postgres**, and they
are intentionally left in place: they *are* the topology under test, so an environment
without them would not verify the thing staging exists to verify. If the credit is
tight enough that ~$29/mo matters more than validating private networking, the endpoints
would need to become conditional — that is a deliberate change, not a default.

**Basic registry means no scoped token.** Staging agents pull with the registry admin
user, which can also push to every repository. Acceptable only because that registry
holds nothing that matters and the environment is disposable; `acr_sku` controls it, and
the deployment switches credential strategy automatically.

`nat_gateway_enabled = false` in staging is a real functional limitation, not just a
saving: Azure has retired default outbound access, so a VNet-injected agent has no route
to the API's public ingress and cannot dial home. Turn it on for the session where
elastic compute is being tested.

## The Terraform runner needs data-plane access

Creating the warehouse filesystem and writing Key Vault secrets are data-plane calls,
and both firewalls deny by default. Set `management_plane_allowed_ips` to the runner's
egress address (`curl -s https://api.ipify.org`), or apply from inside the VNet with
`allow_management_plane_public_access = false`. A resource precondition fails the plan
with this explanation rather than letting the apply get halfway and 403.

## State contains secrets

Generated passwords (Postgres admin, `SECRET_KEY`, the internal API secret, the
Polaris root credential) are `random_password` resources, so they exist in state. The
backend storage account is Entra-only (no account keys), versioned and private. Treat
read access to it as equivalent to read access to the secrets.

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
