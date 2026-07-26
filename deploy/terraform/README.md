# DuckHaven on Azure — Terraform

Provisions DuckHaven on Azure Container Apps with managed PostgreSQL, ADLS Gen2, and
the infrastructure the API needs to create elastic agent container groups at runtime.

Only the DuckHaven API is reachable from the internet. Postgres, storage and Key Vault
sit behind private endpoints; Polaris uses internal-only ingress; elastic agents get
private IPs in a delegated subnet.

> **Status:** foundation only (phase 1 of 7). Networking, Log Analytics and Key Vault
> are in place. Postgres, storage, the registry, the Container Apps environment and
> the apps themselves land in later phases.

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
