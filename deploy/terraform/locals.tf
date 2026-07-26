locals {
  # Long form for resources whose names only need to be unique in the subscription.
  name = "duckhaven-${var.environment}-${var.location_short}"

  # Short form for globally-unique names, which are length-capped (storage accounts
  # allow 24 characters, key vaults 24). "dh" keeps headroom for the suffix.
  name_short = "dh${var.environment}${var.name_suffix}"

  tags = merge(
    {
      app        = "duckhaven"
      env        = var.environment
      managed-by = "terraform"
    },
    var.tags,
  )

  # Named because the registry pull token is scoped to exactly this repository.
  agent_image_repository = "duckhaven-agent"

  # Zone redundancy, network rule sets and retention policies are Premium-only
  # registry features; repository-scoped tokens are too. They are kept as separate
  # locals because they answer different questions, even though both currently reduce
  # to "is this Premium".
  acr_is_premium              = var.acr_sku == "Premium"
  acr_scoped_tokens_supported = var.acr_sku == "Premium"

  # The credential a container instance uses to pull the agent image. A scoped token
  # when the SKU allows one, otherwise the registry admin user -- which is why
  # admin_enabled follows the same condition.
  agent_pull_username = (local.acr_scoped_tokens_supported
    ? azurerm_container_registry_token.agent_pull[0].name
    : azurerm_container_registry.main.admin_username
  )
  agent_pull_password = (local.acr_scoped_tokens_supported
    ? azurerm_container_registry_token_password.agent_pull[0].password1[0].value
    : azurerm_container_registry.main.admin_password
  )

  # Private DNS zones needed by the private endpoints created in this deployment.
  # privatelink.azurecr.io is deliberately absent: the registry keeps a public
  # endpoint because Azure Container Instances pulls images from its own control
  # plane, outside the VNet, and ARM rejects a network-restricted registry at
  # pre-flight. See docs/deployment/azure-terraform.md.
  private_dns_zones = {
    postgres = "privatelink.postgres.database.azure.com"
    blob     = "privatelink.blob.core.windows.net"
    dfs      = "privatelink.dfs.core.windows.net"
    keyvault = "privatelink.vaultcore.azure.net"
  }
}
