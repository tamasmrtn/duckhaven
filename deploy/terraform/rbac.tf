# Every role assignment granted to a workload identity, in one place, so a security
# review can read the deployment's whole permission surface without opening five files.
#
# Two assignments live elsewhere on purpose: the Key Vault and storage grants that the
# Terraform runner itself needs (keyvault.tf, storage.tf). Those exist only to unblock a
# data-plane call during the apply, are paired with a propagation delay, and are not part
# of the running system's permissions.

# The control plane's two elastic-agent assignments are in agents.tf, next to the custom
# role definition they depend on.

# ── DuckHaven API ─────────────────────────────────────────────────────────────

resource "azurerm_role_assignment" "api_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_role_assignment" "api_key_vault_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

# The API reads storage directly in two places: listing during a storage-backend health
# check, and catalog migration IO.
resource "azurerm_role_assignment" "api_storage_blob_data" {
  scope                = azurerm_storage_account.warehouse.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

# Separate from the above, and required by api/src/api/services/staging_presign.py, which
# calls for the account's user delegation key to sign SQL-session staging URLs without
# going through Polaris.
resource "azurerm_role_assignment" "api_storage_blob_delegator" {
  scope                = azurerm_storage_account.warehouse.id
  role_definition_name = "Storage Blob Delegator"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

# ── Database bootstrap ────────────────────────────────────────────────────────

# The only permission this identity has in Azure. Its privilege is inside Postgres
# (it is the server's Entra administrator), and it needs nothing here beyond pulling
# the image its one job runs.
resource "azurerm_role_assignment" "db_bootstrap_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.db_bootstrap.principal_id
}

# ── Polaris ───────────────────────────────────────────────────────────────────

resource "azurerm_role_assignment" "polaris_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.polaris.principal_id
}

resource "azurerm_role_assignment" "polaris_key_vault_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.polaris.principal_id
}

# Polaris is the component that vends storage credentials: DuckHaven's storage backend
# rows carry only a tenant id, and Polaris turns that into a scoped SAS the agents use to
# read and write Iceberg data.
resource "azurerm_role_assignment" "polaris_storage_blob_data" {
  scope                = azurerm_storage_account.warehouse.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.polaris.principal_id
}

# Minting a user-delegation SAS is a separate permission from reading or writing data:
# it needs the account's user delegation key, which Data Contributor alone does not
# grant. Without this, vending fails at the point of signing rather than at access.
resource "azurerm_role_assignment" "polaris_storage_blob_delegator" {
  scope                = azurerm_storage_account.warehouse.id
  role_definition_name = "Storage Blob Delegator"
  principal_id         = azurerm_user_assigned_identity.polaris.principal_id
}
