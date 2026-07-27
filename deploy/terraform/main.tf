data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags

  lifecycle {
    # Caught here because this is the first resource named from it, so the failure
    # arrives at plan time rather than as a malformed name partway through an apply.
    precondition {
      condition     = local.location_short != ""
      error_message = <<-EOT
        No short code is known for location "${var.location}", and it is part of every
        resource name. Set location_short to a 2-4 character abbreviation, or add the
        region to local.location_short_codes in locals.tf.
      EOT
    }
  }
}

# Elastic agents get their own resource group because the reaper reconciles the
# group against DuckHaven's records and terminates every container group tagged
# duckhaven-managed=true that has no live agent row
# (api/src/api/services/compute/reaper.py::_reconcile_leaks). Nothing else may
# share it.
resource "azurerm_resource_group" "agents" {
  name     = "rg-duckhaven-agents-${var.environment}-${local.location_short}"
  location = var.location
  tags     = merge(local.tags, { purpose = "elastic-agents" })
}
