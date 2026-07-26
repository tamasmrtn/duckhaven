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

# ── PostgreSQL ────────────────────────────────────────────────────────────────

variable "postgres_sku_name" {
  description = <<-EOT
    Flexible Server SKU, which encodes both the compute tier and the compute size as
    <tier>_<size>: B_Standard_B1ms is Burstable/1 vCore/2 GiB (the cheapest server
    Azure sells), GP_Standard_D2ds_v5 is GeneralPurpose/2 vCore/8 GiB, and
    MO_Standard_E2ds_v5 is MemoryOptimized. Azure exposes no way to set tier and size
    independently, so one variable covers both.

    GP_Standard_D2ds_v5 is verified available in France Central with ZoneRedundant HA.
    PostgreSQL Flexible Server is offer-restricted in some regions on some
    subscriptions -- check `az postgres flexible-server list-skus -l <region>` before
    changing region, and note that Burstable does not support zone-redundant HA.
  EOT
  type        = string
  default     = "GP_Standard_D2ds_v5"
}

variable "postgres_storage_mb" {
  description = <<-EOT
    Allocated storage in MB: 32768 = 32 GiB, which is the smallest Flexible Server
    allows; 131072 = 128 GiB. Storage can only ever be grown, never shrunk, so start
    small -- an over-provisioned server bills for the larger size permanently.
  EOT
  type        = number
  default     = 131072
}

variable "postgres_storage_tier" {
  description = <<-EOT
    Provisioned IOPS tier for the storage. Billed independently of capacity, so a
    larger tier costs more at the same size. Leave null to take Azure's default for
    the chosen storage_mb (P4 at 32 GiB, P10 at 128 GiB), which is also the cheapest
    tier valid for that size.
  EOT
  type        = string
  default     = null
}

variable "postgres_zone" {
  description = <<-EOT
    Availability zone for the primary. Leave null to let Azure place it, which is the
    safer choice on subscriptions with constrained zone capacity -- a trial account can
    fail to provision into a specific zone. Changing this replaces the server.
  EOT
  type        = string
  default     = "1"
}

variable "postgres_high_availability_enabled" {
  description = <<-EOT
    Zone-redundant HA: a hot standby in a second availability zone with automatic
    failover. Roughly doubles the compute cost, so staging turns it off.
  EOT
  type        = bool
  default     = true
}

variable "postgres_backup_retention_days" {
  description = "Point-in-time restore window, in days."
  type        = number
  default     = 14

  validation {
    condition     = var.postgres_backup_retention_days >= 7 && var.postgres_backup_retention_days <= 35
    error_message = "postgres_backup_retention_days must be between 7 and 35."
  }
}

variable "postgres_geo_redundant_backup_enabled" {
  description = "Replicate backups to the paired region. Cannot be changed after creation."
  type        = bool
  default     = true
}

variable "postgres_entra_admin" {
  description = <<-EOT
    Optional Entra principal (ideally a group) granted PostgreSQL administrator, for
    human break-glass access without sharing the generated admin password. Leave null
    to skip. object_id is the directory object id; principal_name is its display name.
  EOT
  type = object({
    object_id      = string
    principal_name = string
    principal_type = optional(string, "Group")
  })
  default = null
}

# ── Storage ───────────────────────────────────────────────────────────────────

variable "storage_replication_type" {
  description = <<-EOT
    Replication for the Iceberg warehouse. ZRS spreads across three availability
    zones in the region, matching the zone-redundant posture of the rest of the
    deployment; staging can drop to LRS.
  EOT
  type        = string
  default     = "ZRS"

  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "GZRS"], var.storage_replication_type)
    error_message = "storage_replication_type must be one of LRS, ZRS, GRS, GZRS."
  }
}

variable "storage_soft_delete_days" {
  description = <<-EOT
    Blob and container soft-delete window. This is the recovery path for an
    accidental table drop, since Iceberg deletes data files outright.
  EOT
  type        = number
  default     = 7
}

# ── Container registry ────────────────────────────────────────────────────────

variable "acr_sku" {
  description = <<-EOT
    Registry SKU, and the largest single fixed cost in this deployment: Premium is
    roughly 10x Basic per month.

    Premium is the production choice because repository-scoped tokens require it, and
    that scoped, pull-only token is the credential handed to every provisioned agent's
    container spec. On Basic or Standard there is no such token, so the deployment
    falls back to the registry admin user -- push and pull across every repository --
    which is acceptable only for a throwaway environment.
  EOT
  type        = string
  default     = "Premium"

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.acr_sku)
    error_message = "acr_sku must be one of Basic, Standard, Premium."
  }
}

# ── DuckHaven API ─────────────────────────────────────────────────────────────

variable "duckhaven_image_tag" {
  description = <<-EOT
    Tag for the duckhaven-api and duckhaven-agent images in the registry. Both are built
    from the same commit, so they share a tag. No default: an implicit `latest` is how a
    deployment silently changes version, and the tag also determines which agent image
    provisioned container groups run.
  EOT
  type        = string
}

variable "api_min_replicas" {
  description = <<-EOT
    Replica floor.

    Do not raise this above 1 without the per-replica identity change described in
    phase 6 of the plan. Container Apps gives every replica identical environment
    variables and no individually addressable hostname, so today every replica records
    the same owner_url on an agent row, and api/src/api/services/agent_dispatch.py
    short-circuits rather than forwarding -- a query landing on a replica that does not
    hold the agent's WebSocket fails instead of being routed. The failure is silent,
    which is why this is a comment and not merely a default.
  EOT
  type        = number
  default     = 1
}

variable "api_max_replicas" {
  description = "Replica ceiling. See api_min_replicas before raising it above 1."
  type        = number
  default     = 1
}

variable "api_cpu" {
  description = <<-EOT
    vCPU per replica. Container Apps requires exactly 2 GiB of memory per vCPU, so this
    and api_memory move together. Query execution happens on agents, not here.
  EOT
  type        = number
  default     = 1.0
}

variable "api_memory" {
  description = "Memory per replica. Must equal 2 GiB per vCPU."
  type        = string
  default     = "2Gi"
}

# ── Elastic compute ───────────────────────────────────────────────────────────

variable "elastic_compute_enabled" {
  description = <<-EOT
    Whether the control plane may provision agents on demand as container instances.
    Agents are injected into the delegated agent subnet with private addresses only.

    Requires nat_gateway_enabled: an agent dials the control plane at its public
    ingress, and with Azure's default outbound access retired a subnet-injected group
    has no route there without a NAT gateway. It would provision, fail to register, and
    be reaped at the provisioning deadline.
  EOT
  type        = bool
  default     = false

  validation {
    condition     = !var.elastic_compute_enabled || var.nat_gateway_enabled
    error_message = "elastic_compute_enabled requires nat_gateway_enabled: agents cannot reach the control plane without an outbound route."
  }
}

# ── Polaris ───────────────────────────────────────────────────────────────────

variable "polaris_image_tag" {
  description = <<-EOT
    Tag used for both apache/polaris and apache/polaris-admin-tool. The two must match:
    the admin tool bootstraps and migrates the schema the server then reads. Pinned
    rather than `latest` so a redeploy cannot silently move the schema version.
  EOT
  type        = string
  default     = "1.6.0"
}

variable "polaris_realm" {
  description = "Polaris realm bootstrapped and served. Must match the API's POLARIS_REALM."
  type        = string
  default     = "POLARIS"
}

variable "polaris_min_replicas" {
  description = <<-EOT
    Replica floor. Polaris is stateless on Postgres, so it scales horizontally; two
    replicas keep the catalog available while one is replaced. It cannot scale to zero,
    because agents attach catalogs against it directly.
  EOT
  type        = number
  default     = 2
}

variable "polaris_max_replicas" {
  description = "Replica ceiling."
  type        = number
  default     = 2
}

variable "polaris_cpu" {
  description = <<-EOT
    vCPU per replica. Container Apps requires memory to be exactly 2 GiB per vCPU, so
    this and polaris_memory move together. Polaris is a JVM: below about 1 GiB of
    memory it will not start reliably.
  EOT
  type        = number
  default     = 1.0
}

variable "polaris_memory" {
  description = "Memory per replica, as a Container Apps quantity. Must equal 2 GiB per vCPU."
  type        = string
  default     = "2Gi"
}

# ── Cost controls ─────────────────────────────────────────────────────────────

variable "nat_gateway_enabled" {
  description = <<-EOT
    Whether to provision the NAT gateway that gives the agent and Container Apps
    subnets outbound internet access. It bills hourly whether or not traffic flows,
    plus its public IP.

    Disabling it saves that standing cost but breaks elastic compute: Azure has retired
    default outbound access, so a VNet-injected agent would have no route to the API's
    public ingress and could never dial home. Private endpoints and intra-VNet traffic
    still work. Leave it off while testing everything except elastic agents.
  EOT
  type        = bool
  default     = true
}

# ── Observability ─────────────────────────────────────────────────────────────

variable "log_analytics_daily_quota_gb" {
  description = <<-EOT
    Hard cap on daily ingestion, in GB. Log Analytics bills per GB ingested with no
    ceiling by default, which is the one line item here that can run away unattended.
    Set a small cap on non-production environments; -1 means unlimited. Ingestion
    stops for the rest of the UTC day once the cap is hit.
  EOT
  type        = number
  default     = -1
}

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
