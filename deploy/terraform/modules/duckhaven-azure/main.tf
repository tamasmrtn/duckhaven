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

    # DuckHaven cannot run without a catalog, so declining to deploy one has to say
    # where the existing one is. Caught here rather than as a null POLARIS_BASE_URL on
    # an app that then fails every catalog call at runtime.
    precondition {
      condition     = var.polaris_enabled || var.polaris_external_base_url != null
      error_message = "polaris_enabled = false requires polaris_external_base_url: DuckHaven always needs a catalog to talk to."
    }

    # Polaris is the one component here that authenticates with a password, so it
    # cannot run against a server that refuses them.
    precondition {
      condition     = !var.polaris_enabled || !local.postgres_managed_here || var.postgres_password_auth_enabled
      error_message = "polaris_enabled = true requires postgres_password_auth_enabled: Polaris' Quarkus datasource has no Microsoft Entra path."
    }
  }
}

# Elastic agents get their own resource group because the reaper reconciles the
# group against DuckHaven's records and terminates every container group tagged
# duckhaven-managed=true that has no live agent row
# (api/src/api/services/compute/reaper.py::_reconcile_leaks). Nothing else may
# share it.
resource "azurerm_resource_group" "agents" {
  name     = "rg-${var.environment}-${local.workload}-agents-${local.name_tail}"
  location = var.location
  tags     = merge(local.tags, { purpose = "elastic-agents" })
}
