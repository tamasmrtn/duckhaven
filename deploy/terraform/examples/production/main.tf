# The production posture, and the root that serves several environments.
#
# One root with a `.tfvars` file and a state key per environment, rather than a
# directory each: duplicated roots drift, and a fix applied to prod silently misses
# staging. See envs/.
#
# Everything set below is the opinion. What is left as a variable is what genuinely
# differs between one operator's deployment and another's.

variable "environment" {
  description = "Environment name; part of every resource name and the state key."
  type        = string
}

variable "location" {
  description = <<-EOT
    Azure region. Verify your subscription can provision PostgreSQL Flexible Server
    here before applying -- it is offer-restricted in some regions on some
    subscriptions, and the apply fails several minutes in:
      az postgres flexible-server list-skus -l <region>
    Prefer a region with three availability zones, so zone-redundant HA and ZRS storage
    are available.
  EOT
  type        = string
}

variable "name_suffix" {
  description = <<-EOT
    4-8 lowercase alphanumeric characters, making the storage account, registry and Key
    Vault names globally unique. Keep it stable for the lifetime of an environment:
    those names stay reserved during their soft-delete window, so changing it is the
    documented way to recover from a destroy/apply collision.
  EOT
  type        = string
}

variable "duckhaven_image_tag" {
  description = <<-EOT
    Tag of the duckhaven-api and duckhaven-agent images. Pin to a release before this
    environment carries real data: an image tag is how a deploy changes version, and
    `latest` makes that change invisible and irreversible. It also selects the agent
    image provisioned container groups run.
  EOT
  type        = string
}

variable "elastic_compute_enabled" {
  description = "Whether the control plane may provision agents on demand."
  type        = bool
  default     = true
}

variable "postgres_entra_admin" {
  description = <<-EOT
    Optional Entra principal (ideally a group) granted PostgreSQL administrator, for
    human break-glass access. Null to skip.
  EOT
  type = object({
    object_id      = string
    principal_name = string
    principal_type = optional(string, "Group")
  })
  default = null
}

variable "alert_email_addresses" {
  description = "Addresses notified by the alert rules. Empty disables the action group and every alert with it."
  type        = list(string)
  default     = []
}

variable "management_plane_allowed_ips" {
  description = <<-EOT
    Public IPs allowed through the Key Vault and storage firewalls. Creating the
    warehouse filesystem and writing Key Vault secrets are data-plane calls that both
    firewalls deny by default, so the Terraform runner's egress address belongs here
    (`curl -s https://api.ipify.org`) unless the apply runs from inside the VNet.
  EOT
  type        = list(string)
  default     = []
}

module "duckhaven" {
  source = "../../modules/duckhaven-azure"

  environment         = var.environment
  location            = var.location
  name_suffix         = var.name_suffix
  duckhaven_image_tag = var.duckhaven_image_tag

  # Zone-redundant everything: a hot Postgres standby in a second availability zone
  # with automatic failover, ZRS blob storage across three zones, and a Premium
  # registry with zone redundancy. Roughly doubles Postgres compute cost.
  postgres_sku_name                     = "GP_Standard_D2ds_v5"
  postgres_storage_mb                   = 131072
  postgres_zone                         = "1"
  postgres_high_availability_enabled    = true
  postgres_backup_retention_days        = 14
  postgres_geo_redundant_backup_enabled = true
  postgres_entra_admin                  = var.postgres_entra_admin
  storage_replication_type              = "ZRS"
  storage_soft_delete_days              = 7
  acr_sku                               = "Premium"

  # Two replicas each, so the control plane and the catalog stay available while one
  # is replaced. Deliberately fixed rather than autoscaled: agent WebSockets pin to a
  # replica and query work is offloaded to agents, so scaling on HTTP concurrency would
  # both mismeasure load and churn socket ownership.
  api_min_replicas     = 2
  api_max_replicas     = 2
  api_cpu              = 1.0
  api_memory           = "2Gi"
  polaris_min_replicas = 2
  polaris_max_replicas = 2
  polaris_cpu          = 1.0
  polaris_memory       = "2Gi"

  # Agents are injected into the delegated subnet with private addresses, so their
  # result servers are reachable only from this virtual network. They still need the
  # NAT gateway to dial the API's public ingress.
  elastic_compute_enabled = var.elastic_compute_enabled
  nat_gateway_enabled     = true

  log_retention_days    = 30
  alert_email_addresses = var.alert_email_addresses

  allow_management_plane_public_access = true
  management_plane_allowed_ips         = var.management_plane_allowed_ips
}
