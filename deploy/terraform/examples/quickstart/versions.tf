terraform {
  required_version = ">= 1.9"

  # Partial configuration: fill in the storage account from the bootstrap stack's
  # backend_config output, then
  #   terraform init -backend-config=backend.hcl
  #
  # State contains generated secrets (the Polaris root credential, SECRET_KEY, the
  # inter-replica secret, and the Polaris database password), so treat read access to
  # this account as equivalent to read access to them.
  backend "azurerm" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}
