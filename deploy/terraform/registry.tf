# Holds all four images: duckhaven-api, duckhaven-agent, and mirrors of apache/polaris
# and apache/polaris-admin-tool so runtime does not depend on Docker Hub.
#
# This registry keeps a public endpoint, unlike every other service here. Azure
# Container Instances pulls images from its own control plane, outside the VNet: a
# network-restricted registry is rejected at ARM pre-flight with InaccessibleImage in
# about two seconds, even when the container group is injected into a subnet that
# resolves the registry's private endpoint. Since elastic agents are ACI, the registry
# must stay reachable. Access is credential-gated -- the admin user is disabled, so the
# only ways in are managed identity (Container Apps) and the repository-scoped,
# pull-only token below (container instances).
resource "azurerm_container_registry" "main" {
  name                = "cr${local.name_short}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location

  # Premium is required for repository-scoped tokens. On Standard the only credential
  # a container instance could use is the registry admin user, which grants push as
  # well as pull across every repository -- and that credential would sit in the API's
  # environment and in every provisioned agent's container spec. Paying for Premium to
  # avoid handing out registry-admin rights is the trade being made here.
  sku           = "Premium"
  admin_enabled = false

  public_network_access_enabled = true
  zone_redundancy_enabled       = true

  # Set explicitly rather than left to default. Disabling public access on a registry
  # leaves networkRuleSet.defaultAction on Deny, and re-enabling public access does not
  # reset it -- a registry can therefore look public while still refusing every pull.
  # Stating the intended value keeps that state unambiguous.
  network_rule_set {
    default_action = "Allow"
  }

  # Untagged manifests are build residue; they accumulate and are billed as storage.
  retention_policy_in_days = 30

  tags = local.tags
}

# ── Agent pull credential ─────────────────────────────────────────────────────

# Scoped to reading one repository. A leak of this credential exposes the agent image
# and nothing else -- no push rights, no other repository, no registry management.
resource "azurerm_container_registry_scope_map" "agent_pull" {
  name                    = "agent-pull"
  container_registry_name = azurerm_container_registry.main.name
  resource_group_name     = azurerm_resource_group.main.name

  actions = [
    "repositories/${local.agent_image_repository}/content/read",
    "repositories/${local.agent_image_repository}/metadata/read",
  ]
}

resource "azurerm_container_registry_token" "agent_pull" {
  name                    = "agent-pull-token"
  container_registry_name = azurerm_container_registry.main.name
  resource_group_name     = azurerm_resource_group.main.name
  scope_map_id            = azurerm_container_registry_scope_map.agent_pull.id
  enabled                 = true
}

resource "azurerm_container_registry_token_password" "agent_pull" {
  container_registry_token_id = azurerm_container_registry_token.agent_pull.id

  password1 {}
}

# Reaches the API as ELASTIC_REGISTRY_PASSWORD, which it passes to ACI as the
# container group's registry credential.
resource "azurerm_key_vault_secret" "acr_agent_pull_password" {
  name         = "acr-agent-pull-password"
  value        = azurerm_container_registry_token_password.agent_pull.password1[0].value
  key_vault_id = azurerm_key_vault.main.id
  tags         = local.tags

  depends_on = [time_sleep.kv_rbac_propagation]
}
