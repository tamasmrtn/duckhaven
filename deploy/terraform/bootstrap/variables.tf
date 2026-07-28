variable "subscription_id" {
  description = "Azure subscription the state backend lives in."
  type        = string
}

variable "location" {
  description = "Azure region for the state storage account."
  type        = string
  default     = "francecentral"
}

variable "resource_group_name" {
  description = "Resource group holding the state storage account."
  type        = string
  default     = "rg-duckhaven-tfstate"
}

variable "storage_account_name" {
  description = "Globally-unique storage account name for Terraform state."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase alphanumeric characters."
  }
}

variable "container_name" {
  description = "Blob container holding the state files."
  type        = string
  default     = "tfstate"
}
