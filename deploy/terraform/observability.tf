# Bound to the Container Apps environment at creation time rather than attached
# afterwards. The manual RC deployment created its environment with
# --logs-destination none and added a workspace later, which meant the logs needed to
# debug the bring-up did not exist while it was being debugged.
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days

  # Ingestion is billed per GB with no ceiling by default, making this the one line
  # item that can run away while nobody is watching.
  daily_quota_gb = var.log_analytics_daily_quota_gb

  tags = local.tags
}
