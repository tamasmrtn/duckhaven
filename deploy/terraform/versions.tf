terraform {
  required_version = ">= 1.9"

  # Partial configuration: the rest comes from envs/<env>.backend.hcl, which is what
  # keeps one root module serving several environments with isolated state.
  #   terraform init -backend-config=envs/prod.backend.hcl
  backend "azurerm" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}
