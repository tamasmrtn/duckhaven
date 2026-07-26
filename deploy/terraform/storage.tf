# The Iceberg warehouse. Hierarchical namespace is required: DuckHaven registers this
# as an adls_gen2 storage backend with hierarchical = true, and Polaris' AZURE storage
# type vends SAS against the dfs endpoint.
resource "azurerm_storage_account" "warehouse" {
  name                     = "st${local.name_short}"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = var.storage_replication_type
  account_kind             = "StorageV2"
  is_hns_enabled           = true

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false

  # No account keys at all. Polaris vends short-lived SAS using its managed identity,
  # and the API mints user-delegation SAS the same way, so nothing needs a shared key
  # -- and a key that does not exist cannot leak into a config file.
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  public_network_access_enabled = var.allow_management_plane_public_access

  network_rules {
    # Application traffic arrives over the blob and dfs private endpoints, which
    # bypass these rules. The public endpoint exists only so a Terraform runner
    # outside the VNet can create the filesystem below.
    default_action = "Deny"
    bypass         = ["AzureServices"]
    ip_rules       = var.management_plane_allowed_ips
  }

  blob_properties {
    # Iceberg deletes data files outright when a table is dropped or compacted, so
    # soft delete is the only thing standing between a mistake and data loss.
    delete_retention_policy {
      days = var.storage_soft_delete_days
    }

    container_delete_retention_policy {
      days = var.storage_soft_delete_days
    }
  }

  tags = local.tags
}

# ── Deployer access ───────────────────────────────────────────────────────────

# Creating the filesystem is a data-plane operation. With shared_access_key_enabled
# false it must authenticate as the caller's Entra identity, which needs a data-plane
# role the account does not grant by default.
resource "azurerm_role_assignment" "deployer_storage_blob" {
  scope                = azurerm_storage_account.warehouse.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "time_sleep" "storage_rbac_propagation" {
  depends_on      = [azurerm_role_assignment.deployer_storage_blob]
  create_duration = "30s"
}

# Iceberg root: abfss://warehouse@<account>.dfs.core.windows.net/duckhaven/
#
# Requires the runner to reach the account's data plane: either list its egress IP in
# management_plane_allowed_ips, or apply from inside the VNet.
resource "azurerm_storage_data_lake_gen2_filesystem" "warehouse" {
  name               = "warehouse"
  storage_account_id = azurerm_storage_account.warehouse.id

  depends_on = [time_sleep.storage_rbac_propagation]

  lifecycle {
    # Caught here rather than as a 403 partway through the apply, once the account
    # already exists. The firewall denies by default, so a runner outside the VNet can
    # only reach the data plane if its address is listed.
    precondition {
      condition = (
        !var.allow_management_plane_public_access
        || length(var.management_plane_allowed_ips) > 0
      )
      error_message = <<-EOT
        Creating the warehouse filesystem is a data-plane call that the storage
        firewall will refuse: allow_management_plane_public_access is true but
        management_plane_allowed_ips is empty, so the public endpoint denies every
        address.

        Either add the Terraform runner's egress IP to management_plane_allowed_ips
        (`curl -s https://api.ipify.org`), or run the apply from inside the VNet and
        set allow_management_plane_public_access = false.
      EOT
    }
  }
}

# ── Private endpoints ─────────────────────────────────────────────────────────

# Two endpoints, not one. Polaris and the agents talk to the dfs endpoint (abfss://),
# while api/src/api/services/staging_presign.py rewrites .dfs. to .blob. to mint
# user-delegation SAS, so both hostnames must resolve privately.
module "pe_storage_blob" {
  source = "./modules/private-endpoint"

  name                 = "pe-st-blob-${local.name}"
  location             = var.location
  resource_group_name  = azurerm_resource_group.main.name
  subnet_id            = azurerm_subnet.pe.id
  target_resource_id   = azurerm_storage_account.warehouse.id
  subresource_names    = ["blob"]
  private_dns_zone_ids = [azurerm_private_dns_zone.main["blob"].id]
  tags                 = local.tags
}

module "pe_storage_dfs" {
  source = "./modules/private-endpoint"

  name                 = "pe-st-dfs-${local.name}"
  location             = var.location
  resource_group_name  = azurerm_resource_group.main.name
  subnet_id            = azurerm_subnet.pe.id
  target_resource_id   = azurerm_storage_account.warehouse.id
  subresource_names    = ["dfs"]
  private_dns_zone_ids = [azurerm_private_dns_zone.main["dfs"].id]
  tags                 = local.tags
}

# No blob lifecycle rule for SQL-session staging files.
#
# api/src/api/services/session_credentials.py::staging_uri_for builds
# <catalog storage base>/_staging/<session_id>/, so "_staging" appears part-way down a
# per-catalog path. Azure lifecycle filters match a literal prefix only -- no
# wildcards -- so no rule can express "any path containing /_staging/" without also
# matching the table data beside it. Cleaning these up needs either a dedicated
# container or a top-level staging prefix on the application side; a rule here would
# match nothing and imply a retention guarantee that does not exist.
