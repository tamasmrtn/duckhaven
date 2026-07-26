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
