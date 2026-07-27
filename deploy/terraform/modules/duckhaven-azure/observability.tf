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

# ── Diagnostics ───────────────────────────────────────────────────────────────

# The Container Apps environment already forwards container and system logs via the
# workspace binding above. These cover the resources that would otherwise be silent:
# without them a failed private-endpoint connection, a throttled storage request or a
# vault access denial leaves no trace anywhere.
#
# category_group = "allLogs" rather than an explicit category list, because the
# available categories differ per resource type and change between API versions; a
# missing category fails the apply, and a stale list silently stops collecting.
resource "azurerm_monitor_diagnostic_setting" "postgres" {
  # Nothing to collect from a server this deployment does not own; its logs and
  # metrics belong to whoever runs it.
  count = local.postgres_managed_here ? 1 : 0

  name                       = "diag-to-law"
  target_resource_id         = azurerm_postgresql_flexible_server.main[0].id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# Targets the blob service, not the account: data-plane read/write/delete logging lives
# on the sub-resource. ADLS Gen2 dfs operations are recorded here too.
resource "azurerm_monitor_diagnostic_setting" "storage_blob" {
  name                       = "diag-to-law"
  target_resource_id         = "${azurerm_storage_account.warehouse.id}/blobServices/default"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "Transaction"
  }
}

resource "azurerm_monitor_diagnostic_setting" "registry" {
  name                       = "diag-to-law"
  target_resource_id         = azurerm_container_registry.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# The audit trail for secret reads. With managed identities doing the reading, this is
# how you see which workload used which secret and when.
resource "azurerm_monitor_diagnostic_setting" "key_vault" {
  name                       = "diag-to-law"
  target_resource_id         = azurerm_key_vault.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category_group = "audit"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# ── Alerts ────────────────────────────────────────────────────────────────────

resource "azurerm_monitor_action_group" "main" {
  count = length(var.alert_email_addresses) > 0 ? 1 : 0

  name                = "ag-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "duckhaven"

  dynamic "email_receiver" {
    for_each = var.alert_email_addresses

    content {
      name                    = "email-${email_receiver.key}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }

  tags = local.tags
}

# Storage can only grow, never shrink, and a full disk takes the database read-only.
# This is the alert that matters most: it is the one failure here that is unrecoverable
# without a maintenance window.
resource "azurerm_monitor_metric_alert" "postgres_storage" {
  count = local.postgres_managed_here && length(var.alert_email_addresses) > 0 ? 1 : 0

  name                = "alert-psql-storage-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_postgresql_flexible_server.main[0].id]
  description         = "PostgreSQL storage is nearly full. A full disk takes the server read-only."
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT15M"

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "storage_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 85
  }

  action {
    action_group_id = azurerm_monitor_action_group.main[0].id
  }

  tags = local.tags
}

resource "azurerm_monitor_metric_alert" "postgres_cpu" {
  count = local.postgres_managed_here && length(var.alert_email_addresses) > 0 ? 1 : 0

  name                = "alert-psql-cpu-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_postgresql_flexible_server.main[0].id]
  description         = "PostgreSQL CPU is saturated; the control plane will feel slow."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT30M"

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "cpu_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 80
  }

  action {
    action_group_id = azurerm_monitor_action_group.main[0].id
  }

  tags = local.tags
}

# A provisioning failure is otherwise invisible: the backend does not wait on the ARM
# poller, so a quota, image or subnet error surfaces only as an agent row that sits in
# "provisioning" until the deadline expires. The log line comes from
# api/src/api/services/compute/service.py::_mint_and_provision.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "elastic_provision_failed" {
  count = length(var.alert_email_addresses) > 0 ? 1 : 0

  name                 = "alert-elastic-provision-${local.name}"
  resource_group_name  = azurerm_resource_group.main.name
  location             = var.location
  scopes               = [azurerm_log_analytics_workspace.main.id]
  description          = "An elastic agent failed to provision."
  severity             = 2
  evaluation_frequency = "PT15M"
  window_duration      = "PT15M"

  criteria {
    query                   = <<-KQL
      ContainerAppConsoleLogs_CL
      | where ContainerAppName_s == "api"
      | where Log_s has "Elastic provision failed"
    KQL
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.main[0].id]
  }

  tags = local.tags
}

# Cost guard. Elastic agents are supposed to be short-lived; a stuck reaper or a
# provisioning loop shows up as container groups that never go away.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "elastic_agents_long_lived" {
  count = length(var.alert_email_addresses) > 0 ? 1 : 0

  name                 = "alert-elastic-longlived-${local.name}"
  resource_group_name  = azurerm_resource_group.main.name
  location             = var.location
  scopes               = [azurerm_log_analytics_workspace.main.id]
  description          = "Elastic agents are running for longer than their maximum lifetime allows."
  severity             = 3
  evaluation_frequency = "PT1H"
  window_duration      = "PT1H"

  criteria {
    query                   = <<-KQL
      AzureMetrics
      | where ResourceProvider == "MICROSOFT.CONTAINERINSTANCE"
      | summarize groups = dcount(Resource) by bin(TimeGenerated, 1h)
    KQL
    time_aggregation_method = "Maximum"
    metric_measure_column   = "groups"
    threshold               = 4
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.main[0].id]
  }

  tags = local.tags
}
