variable "subscription_id" {
  description = "Azure subscription the deployment lives in."
  type        = string
}

variable "environment" {
  description = "Environment name; part of every resource name and the state key."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{2,10}$", var.environment))
    error_message = "environment must be 2-10 lowercase alphanumeric characters."
  }
}

variable "location" {
  description = <<-EOT
    Azure region. Defaults to francecentral: PostgreSQL Flexible Server cannot be
    provisioned in germanywestcentral (or westeurope/eastus/eastus2) on this
    subscription -- the capability API reports OfferRestricted. France Central is
    the nearest supported EU region with three availability zones and offers
    Standard_D2ds_v5 with zone-redundant HA. See docs/deployment/azure-terraform.md.
  EOT
  type        = string
  default     = "francecentral"
}

variable "location_short" {
  description = "Short region code used in resource names."
  type        = string
  default     = "frc"
}

variable "name_suffix" {
  description = <<-EOT
    Suffix for globally-unique names (storage account, registry, key vault). Keep it
    stable for the lifetime of an environment: these names stay reserved during the
    soft-delete window, so changing it is the documented way to recover from a
    destroy/apply cycle that collides with a soft-deleted name.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{4,8}$", var.name_suffix))
    error_message = "name_suffix must be 4-8 lowercase alphanumeric characters."
  }
}

variable "tags" {
  description = "Additional tags merged into every resource."
  type        = map(string)
  default     = {}
}

# ── Networking ────────────────────────────────────────────────────────────────

variable "vnet_address_space" {
  description = "VNet CIDR. 10.42.4.0/22 inside it is deliberately left unallocated."
  type        = string
  default     = "10.42.0.0/16"
}

variable "subnet_prefix_aca" {
  description = <<-EOT
    Container Apps infrastructure subnet. Must be at least /27 for a
    workload-profiles environment and is IMMUTABLE once the environment exists, so
    it is sized /23 up front.
  EOT
  type        = string
  default     = "10.42.0.0/23"
}

variable "subnet_prefix_pe" {
  description = "Private endpoint subnet."
  type        = string
  default     = "10.42.2.0/24"
}

variable "subnet_prefix_aci" {
  description = "Elastic agent subnet, delegated to Microsoft.ContainerInstance/containerGroups."
  type        = string
  default     = "10.42.3.0/24"
}

variable "agent_result_port" {
  description = <<-EOT
    Port the agent result server listens on. The API fetches result Parquet from it
    over plain HTTP, which is acceptable only because the hop stays inside the VNet;
    nsg-aci restricts inbound on this port to the Container Apps subnet. Hardcoded
    to 8001 in api/src/api/services/compute/azure_aci.py.
  EOT
  type        = number
  default     = 8001
}

# ── Observability ─────────────────────────────────────────────────────────────

variable "log_retention_days" {
  description = "Log Analytics retention in days."
  type        = number
  default     = 30

  validation {
    condition     = var.log_retention_days >= 30 && var.log_retention_days <= 730
    error_message = "log_retention_days must be between 30 and 730."
  }
}

# ── Management-plane access ───────────────────────────────────────────────────

variable "allow_management_plane_public_access" {
  description = <<-EOT
    Whether Key Vault and the storage account keep a public endpoint open for the
    Terraform runner and CI. Application traffic always uses the private endpoints.
    Set false when applying from a runner inside the VNet, which closes the public
    endpoint entirely.
  EOT
  type        = bool
  default     = true
}

variable "management_plane_allowed_ips" {
  description = <<-EOT
    Public IPs allowed through the Key Vault and storage firewalls when
    allow_management_plane_public_access is true. Leave empty only if you accept an
    open (but Entra-authenticated) endpoint.
  EOT
  type        = list(string)
  default     = []
}
