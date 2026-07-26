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

  api_image_repository = "duckhaven-api"

  # Named separately because the registry pull token is scoped to exactly this repository.
  agent_image_repository = "duckhaven-agent"

  aca_workload_profile = "Consumption"

  # The root principal the bootstrap creates and the API authenticates as. Not a
  # variable: it has to match api/src/api/config.py's polaris_client_id default, and
  # the two would drift silently if both were configurable.
  polaris_client_id = "root"

  # Mirrored into the registry rather than pulled from Docker Hub, so a deploy does not
  # depend on Docker Hub availability or anonymous pull limits. The import is a
  # prerequisite of the first apply -- see README.
  polaris_image            = "${azurerm_container_registry.main.login_server}/polaris:${var.polaris_image_tag}"
  polaris_admin_tool_image = "${azurerm_container_registry.main.login_server}/polaris-admin-tool:${var.polaris_image_tag}"

  # The API's own public hostname, built from the environment's domain rather than read
  # off the app resource. Reading it back would make ELASTIC_CONTROL_PLANE_URL -- an
  # environment variable *on that app* -- depend on the app itself, which is a dependency
  # cycle Terraform cannot resolve. The domain is known as soon as the environment
  # exists, and an external-ingress app is always <name>.<domain>.
  api_fqdn = "api.${azurerm_container_app_environment.main.default_domain}"

  # Polaris is reached over internal ingress, which resolves only inside the VNet. The
  # platform issues a certificate covering this name, so the API and the agents can
  # verify TLS against it (confirmed during the Phase 0 spike).
  polaris_internal_url = "https://polaris.internal.${azurerm_container_app_environment.main.default_domain}"

  # sslmode=require rather than the driver default of prefer: Azure enforces TLS, and
  # stating it means a misconfiguration fails loudly instead of silently downgrading.
  polaris_jdbc_url = join("", [
    "jdbc:postgresql://",
    azurerm_postgresql_flexible_server.main.fqdn,
    ":5432/",
    azurerm_postgresql_flexible_server_database.polaris.name,
    "?sslmode=require",
  ])

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
