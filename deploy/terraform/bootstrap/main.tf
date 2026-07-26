# One-time bootstrap of the Terraform state backend.
#
# This stack keeps LOCAL state, because it is what creates the remote backend the
# main stack uses. Run it once per subscription; its own state file is disposable --
# every resource here is trivially recreatable and importable.

terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  subscription_id     = var.subscription_id
  storage_use_azuread = true
  features {}
}

locals {
  # "shared" rather than a single environment: one state account holds the state for
  # every environment, keyed per environment inside the container.
  tags = {
    app        = "duckhaven"
    env        = "shared"
    purpose    = "terraform-state"
    managed-by = "terraform"
  }
}

resource "azurerm_resource_group" "state" {
  name     = var.resource_group_name
  location = var.location

  tags = local.tags
}

resource "azurerm_storage_account" "state" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.state.name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "ZRS"
  account_kind             = "StorageV2"

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false

  # State contains generated passwords. Entra-only access means there is no account
  # key to leak, and the backend authenticates with use_azuread_auth = true.
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  tags = local.tags
}

resource "azurerm_storage_container" "state" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

output "backend_config" {
  description = "Values for envs/<env>.backend.hcl."
  value = {
    resource_group_name  = azurerm_resource_group.state.name
    storage_account_name = azurerm_storage_account.state.name
    container_name       = azurerm_storage_container.state.name
    use_azuread_auth     = true
  }
}
