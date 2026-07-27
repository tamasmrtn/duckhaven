# The subscription comes from the environment (ARM_SUBSCRIPTION_ID, or the Azure CLI's
# active subscription) rather than a variable: one less required input, and it cannot
# disagree with the credentials the apply is actually running under.
#
# The first apply against a fresh subscription may spend several minutes registering
# resource providers (Microsoft.Network alone took ~4.5 minutes during validation).
# That is the provider auto-registering; let it finish rather than interrupting.
provider "azurerm" {
  # Data-plane calls (creating the ADLS filesystem, writing Key Vault secrets) use
  # Entra ID rather than shared keys, because the storage account sets
  # shared_access_key_enabled = false.
  storage_use_azuread = true

  features {
    key_vault {
      # Never purge on destroy: the vault name stays reserved during the 90-day
      # soft-delete window either way, and purging destroys recoverable secrets.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}
