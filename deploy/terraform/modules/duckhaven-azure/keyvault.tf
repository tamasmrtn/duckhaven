resource "azurerm_key_vault" "main" {
  name                = "kv-${local.name_short}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  # RBAC rather than access policies, so vault permissions are auditable alongside
  # every other role assignment in rbac.tf.
  rbac_authorization_enabled = true

  # Purge protection cannot be disabled once enabled, and the name stays reserved for
  # the soft-delete window. That is a deliberate trade: recovering an accidentally
  # deleted vault matters more than being able to immediately reuse the name. Change
  # name_suffix to recover from a collision.
  purge_protection_enabled   = true
  soft_delete_retention_days = 90

  public_network_access_enabled = var.allow_management_plane_public_access

  network_acls {
    # Application traffic arrives over the private endpoint below, which bypasses
    # these rules entirely; the public endpoint exists only so a Terraform runner
    # outside the VNet can write secrets.
    #
    # No virtual_network_subnet_ids: a Key Vault network rule requires the subnet to
    # carry the Microsoft.KeyVault service endpoint, which these subnets deliberately
    # do not (the private endpoint is the access path). Listing a subnet without it
    # fails the apply.
    default_action = "Deny"
    bypass         = "AzureServices"
    ip_rules       = var.management_plane_allowed_ips
  }

  tags = local.tags
}

module "pe_keyvault" {
  source = "../private-endpoint"

  name                 = "pe-kv-${local.name}"
  location             = var.location
  resource_group_name  = azurerm_resource_group.main.name
  subnet_id            = azurerm_subnet.pe.id
  target_resource_id   = azurerm_key_vault.main.id
  subresource_names    = ["vault"]
  private_dns_zone_ids = [azurerm_private_dns_zone.main["keyvault"].id]
  tags                 = local.tags
}

# ── Deployer access ───────────────────────────────────────────────────────────

# With RBAC authorization the principal running Terraform has no data-plane rights on
# a vault it just created, so it cannot write the secrets below without this.
resource "azurerm_role_assignment" "deployer_kv_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Azure RBAC is eventually consistent; a secret write immediately after the
# assignment intermittently fails with 403.
resource "time_sleep" "kv_rbac_propagation" {
  depends_on      = [azurerm_role_assignment.deployer_kv_secrets]
  create_duration = "30s"
}

# ── Secrets ───────────────────────────────────────────────────────────────────

# All alphanumeric on purpose. This password reaches Polaris through a JDBC URL
# assembled by interpolation (local.polaris_jdbc_url), so one containing @ : / # or ?
# would corrupt it. 48 alphanumeric characters is far more entropy than a
# symbol-laden shorter password.
#
# The API no longer uses it: it authenticates with its managed identity. This is the
# Polaris credential, and the one the two bootstrap jobs use.
resource "random_password" "postgres_admin" {
  length  = 48
  special = false
}

resource "random_password" "api_secret_key" {
  length  = 64
  special = false
}

resource "random_password" "internal_api_secret" {
  length  = 64
  special = false
}

# Replaces the "s3cr3t" default that ships in api/src/api/config.py. It is injected
# into every provisioned agent as POLARIS_CLIENT_SECRET, so it also travels through
# ACI environment variables.
resource "random_password" "polaris_client_secret" {
  length  = 48
  special = false
}

locals {
  key_vault_secrets = {
    postgres-admin-password = random_password.postgres_admin.result
    api-secret-key          = random_password.api_secret_key.result
    internal-api-secret     = random_password.internal_api_secret.result
    polaris-client-secret   = random_password.polaris_client_secret.result
  }
}

resource "azurerm_key_vault_secret" "main" {
  for_each = local.key_vault_secrets

  name         = each.key
  value        = each.value
  key_vault_id = azurerm_key_vault.main.id
  tags         = local.tags

  depends_on = [time_sleep.kv_rbac_propagation]
}
