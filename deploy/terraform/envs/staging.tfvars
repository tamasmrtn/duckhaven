subscription_id = "460eb8b9-9a3e-42f1-ab7f-d2963470725e"
environment     = "staging"

location       = "francecentral"
location_short = "frc"

name_suffix = "b3n8q4"

# This environment exists to verify that the topology applies and works. It is not
# production-shaped and is not meant to survive anything. Every value below is chosen
# to be the cheapest Azure offers, because it runs on a trial subscription with a fixed
# credit -- see the cost notes in ../README.md.
#
# The single biggest saving is not in this file: destroy the environment between test
# runs. Idle infrastructure still bills, and everything here is reproducible from
# `terraform apply`.

# A separate VNet range so staging and prod can be peered or share a runner later
# without renumbering. Networking itself is free.
vnet_address_space = "10.43.0.0/16"
subnet_prefix_aca  = "10.43.0.0/23"
subnet_prefix_pe   = "10.43.2.0/24"
subnet_prefix_aci  = "10.43.3.0/24"

# Burstable, 1 vCore / 2 GiB, at the 32 GiB storage floor: the cheapest server Azure
# sells. No hot standby, no cross-region backup, shortest retention. Burstable does not
# support zone-redundant HA anyway. zone is null so Azure places the server wherever it
# has capacity, which a trial subscription may not have in a specific zone.
postgres_sku_name                     = "B_Standard_B1ms"
postgres_storage_mb                   = 32768
postgres_storage_tier                 = null
postgres_zone                         = null
postgres_high_availability_enabled    = false
postgres_backup_retention_days        = 7
postgres_geo_redundant_backup_enabled = false

# Single-zone storage and the shortest soft-delete window, so deleted blobs stop being
# billed almost immediately.
storage_replication_type = "LRS"
storage_soft_delete_days = 1

# Basic instead of Premium, roughly a tenth of the cost. The trade is real: Basic has no
# repository-scoped tokens, so provisioned agents pull with the registry admin user,
# which can also push to every repository. Acceptable only because this registry holds
# nothing that matters and the environment is disposable.
acr_sku = "Basic"

# One small replica instead of two. Container Apps bills per vCPU-second and
# GiB-second, so replica count and size are the levers, and an always-on app is a
# standing cost. 0.5 vCPU / 1 GiB is the smallest a Quarkus JVM starts reliably in --
# Container Apps requires exactly 2 GiB per vCPU, so the two move together. There is no
# scale-to-zero option: agents attach catalogs against Polaris directly.
polaris_min_replicas = 1
polaris_max_replicas = 1
polaris_cpu          = 0.5
polaris_memory       = "1Gi"

# One small replica. Staging is not trying to survive a replica being replaced, and a
# single replica also sidesteps cross-replica dispatch entirely. 0.5 vCPU / 1 GiB is
# enough to run migrations and serve the SPA; query execution happens on agents.
api_min_replicas = 1
api_max_replicas = 1
api_cpu          = 0.5
api_memory       = "1Gi"

# Off by default here: the gateway and its public IP bill hourly regardless of traffic.
# Turn this on for the session where elastic agents are being tested -- without it a
# VNet-injected agent has no outbound route and cannot dial the control plane.
nat_gateway_enabled = false

# Disposable environment, so a floating tag is acceptable here.
duckhaven_image_tag = "latest"

# Retention is already at the 30-day floor; the cap is what actually bounds the bill,
# since ingestion is charged per GB.
log_retention_days           = 30
log_analytics_daily_quota_gb = 1

# Replace with the Terraform runner's egress address (`curl -s https://api.ipify.org`).
# Without it the storage firewall refuses the data-plane call that creates the warehouse
# filesystem, and the plan fails with that explanation.
allow_management_plane_public_access = true
management_plane_allowed_ips         = []
