data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# Elastic agents get their own resource group because the reaper reconciles the
# group against DuckHaven's records and terminates every container group tagged
# duckhaven-managed=true that has no live agent row
# (api/src/api/services/compute/reaper.py::_reconcile_leaks). Nothing else may
# share it.
resource "azurerm_resource_group" "agents" {
  name     = "rg-duckhaven-agents-${var.environment}-${var.location_short}"
  location = var.location
  tags     = merge(local.tags, { purpose = "elastic-agents" })
}
