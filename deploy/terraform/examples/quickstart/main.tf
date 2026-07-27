# The cheapest configuration that actually works, for trying DuckHaven on Azure or
# running a disposable environment.
#
# What it does NOT reduce is the network topology: private endpoints, VNet injection,
# the delegated agent subnet and its security group are the same as production. Agents
# move and query real data, so that is the deployment, not a tier of it. The savings
# here come from SKUs and replica counts, which is where the money actually is.
#
# Elastic compute is off, and with it the NAT gateway -- both bill hourly whether or not
# anything runs. Turn them on together for a session that exercises agents.

variable "environment" {
  description = "Environment name; part of every resource name and the state key."
  type        = string
  default     = "dev"
}

variable "location" {
  description = <<-EOT
    Azure region. Check your subscription can provision PostgreSQL Flexible Server here
    first: `az postgres flexible-server list-skus -l <region>`.
  EOT
  type        = string
}

variable "name_suffix" {
  description = <<-EOT
    4-8 lowercase alphanumeric characters, making the storage account, registry and Key
    Vault names globally unique. Keep it stable: those names stay reserved during their
    soft-delete window.
  EOT
  type        = string
}

variable "duckhaven_image_tag" {
  description = "Tag of the duckhaven-api and duckhaven-agent images in the registry."
  type        = string
}

module "duckhaven" {
  source = "../../modules/duckhaven-azure"

  environment         = var.environment
  location            = var.location
  name_suffix         = var.name_suffix
  duckhaven_image_tag = var.duckhaven_image_tag

  # Burstable, 1 vCore / 2 GiB, at the 32 GiB storage floor: the cheapest server Azure
  # sells. Burstable does not support zone-redundant HA anyway, and postgres_zone is
  # null so Azure places the server wherever it has capacity -- a trial subscription may
  # have none in a specific zone.
  postgres_sku_name                     = "B_Standard_B1ms"
  postgres_storage_mb                   = 32768
  postgres_zone                         = null
  postgres_high_availability_enabled    = false
  postgres_backup_retention_days        = 7
  postgres_geo_redundant_backup_enabled = false

  # Single-zone storage and the shortest soft-delete window, so deleted blobs stop
  # being billed almost immediately.
  storage_replication_type = "LRS"
  storage_soft_delete_days = 1

  # Basic is roughly a tenth of Premium and costs nothing in security: every pull is
  # authenticated by a managed identity on either SKU. What it gives up is zone
  # redundancy, network rule sets, retention policies and included throughput.
  acr_sku = "Basic"

  # One small replica each. Container Apps bills per vCPU-second and GiB-second, so
  # replica count and size are the levers. 0.5 vCPU / 1 GiB is the smallest a Quarkus
  # JVM starts reliably in, and Container Apps requires exactly 2 GiB per vCPU, so the
  # two always move together. Neither app can scale to zero: agents attach catalogs
  # against Polaris directly.
  api_min_replicas     = 1
  api_max_replicas     = 1
  api_cpu              = 0.5
  api_memory           = "1Gi"
  polaris_min_replicas = 1
  polaris_max_replicas = 1
  polaris_cpu          = 0.5
  polaris_memory       = "1Gi"

  # Off together: a VNet-injected agent has no route to the API's public ingress
  # without the gateway, so elastic compute cannot work, and the module rejects the
  # combination rather than letting agents fail to register.
  nat_gateway_enabled     = false
  elastic_compute_enabled = false

  # Retention is already at the 30-day floor; the cap is what actually bounds the bill,
  # since Log Analytics charges per GB ingested with no ceiling by default.
  log_retention_days           = 30
  log_analytics_daily_quota_gb = 1

  # Creating the warehouse filesystem and writing Key Vault secrets are data-plane
  # calls that both firewalls deny by default. Put the Terraform runner's egress
  # address here (`curl -s https://api.ipify.org`) or the plan fails saying so.
  allow_management_plane_public_access = true
  management_plane_allowed_ips         = []
}
