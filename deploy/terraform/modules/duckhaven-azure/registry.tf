# Holds all four images: duckhaven-api, duckhaven-agent, and mirrors of apache/polaris
# and apache/polaris-admin-tool so runtime does not depend on Docker Hub.
#
# This registry keeps a public endpoint, unlike every other service here. Azure
# Container Instances pulls images from its own control plane, outside the VNet: a
# network-restricted registry is rejected at ARM pre-flight with InaccessibleImage in
# about two seconds, even when the container group is injected into a subnet that
# resolves the registry's private endpoint. Since elastic agents are ACI, the registry
# must stay reachable.
#
# There are no registry passwords anywhere in this deployment. The admin user stays
# disabled and the only way in is a managed identity holding AcrPull: the app
# identities for Container Apps, and the agent identity below for container instances.
resource "azurerm_container_registry" "main" {
  name                = "cr${local.name_short}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location

  sku = var.acr_sku

  # No credential is ever issued from this registry, so the admin user has no purpose.
  admin_enabled = false

  public_network_access_enabled = true

  # Zone redundancy, network rule sets and retention policies are all Premium-only
  # features, so they follow the SKU rather than being stated unconditionally.
  zone_redundancy_enabled = local.acr_is_premium

  # Set explicitly rather than left to default. Disabling public access on a registry
  # leaves networkRuleSet.defaultAction on Deny, and re-enabling public access does not
  # reset it -- a registry can therefore look public while still refusing every pull.
  # Stating the intended value keeps that state unambiguous.
  dynamic "network_rule_set" {
    for_each = local.acr_is_premium ? [1] : []

    content {
      default_action = "Allow"
    }
  }

  # Untagged manifests are build residue; they accumulate and are billed as storage.
  retention_policy_in_days = local.acr_is_premium ? 30 : null

  tags = local.tags
}

# ── Agent pull identity ───────────────────────────────────────────────────────

# The identity every provisioned agent container group carries, and pulls its image
# as. Separate from the API's own identity so an agent's credential grants exactly one
# thing -- reading this registry -- and nothing the control plane can do.
#
# ACI supports only *user-assigned* identities for image pull, which is why this is a
# standalone resource rather than a system-assigned identity on each group.
resource "azurerm_user_assigned_identity" "agent" {
  name                = "id-duckhaven-agent-${var.environment}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "agent_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.agent.principal_id
}
