# Production

The production posture, and the root that serves several environments.

Zone-redundant throughout: a hot PostgreSQL standby in a second availability zone with
automatic failover, ZRS blob storage across three zones, a zone-redundant Premium
registry, and two replicas of each app so the control plane and the catalog stay
available while one is replaced.

```sh
export ARM_SUBSCRIPTION_ID=<your subscription>

terraform init -backend-config=envs/prod.backend.hcl
terraform apply -var-file=envs/prod.tfvars
```

Switching environments re-initialises the backend:

```sh
terraform init -reconfigure -backend-config=envs/staging.backend.hcl
terraform apply -var-file=envs/staging.tfvars
```

## One root, several environments

Rather than a directory per environment. Duplicated roots drift: a fix applied to prod
silently misses staging. Environments differ only by their `.tfvars` and their state
key.

`envs/staging.tfvars` is therefore **production-shaped**, and costs roughly what
production costs. That is deliberate — an environment that differs from production does
not verify production. For a cheap environment to try DuckHaven in, use
[`../quickstart`](../quickstart), which keeps the same network topology and drops the
SKUs.

## What you have to supply

Everything else is set in `main.tf`, which is where this example's opinion lives.

| Input | Why it cannot have a default |
|---|---|
| `location` | PostgreSQL Flexible Server is offer-restricted in some regions on some subscriptions. Check with `az postgres flexible-server list-skus -l <region>` first, and prefer a three-zone region. |
| `name_suffix` | Makes the storage account, registry and Key Vault names globally unique to you. Keep it stable — those names stay reserved during their soft-delete window. |
| `duckhaven_image_tag` | An implicit `latest` is how a deployment silently changes version. It also selects the agent image provisioned container groups run. |
| `management_plane_allowed_ips` | The Terraform runner's egress address. Creating the warehouse filesystem is a data-plane call the storage firewall otherwise refuses. |
| `alert_email_addresses` | Empty disables every alert. Set it once someone is actually on the hook. |

`postgres_entra_admin` is optional: an Entra group granted PostgreSQL administrator for
human break-glass access, separate from the identity the API connects with.

See [`../../README.md`](../../README.md) for the first-apply sequence and the two
bootstrap jobs.
