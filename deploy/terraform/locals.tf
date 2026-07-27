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

  # Polaris' hostname, which depends on how it has to be reached.
  #
  # Internal ingress is the default and the better posture: the name resolves only
  # inside the environment and there is no public listener at all.
  #
  # Elastic compute forces the alternative. An agent runs in its own subnet, outside the
  # environment, and only replicas *inside* an environment resolve an internal-ingress
  # app -- measured from the agent subnet, Polaris' internal name resolved to the
  # environment's public address and returned 404. A private endpoint cannot fix it
  # either: Azure rejects one unless the environment's public access is disabled, which
  # would take the API offline with it. So when agents exist, Polaris takes external
  # ingress locked to this deployment's single egress address (see polaris.tf).
  #
  # The platform issues a certificate covering either name, so callers verify TLS
  # normally.
  polaris_url = var.elastic_compute_enabled ? (
    "https://polaris.${azurerm_container_app_environment.main.default_domain}"
    ) : (
    "https://polaris.internal.${azurerm_container_app_environment.main.default_domain}"
  )

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
  # registry features. Nothing about *access* depends on the SKU: every pull is
  # managed-identity authenticated on Basic just as on Premium.
  acr_is_premium = var.acr_sku == "Premium"

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
